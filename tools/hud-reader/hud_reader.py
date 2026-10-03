"""Read only calibrated visible HUD pixels. No game input or process inspection."""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
DISTANCE = (710, 138, 804, 154)
SPEED = (1360, 1112, 1440, 1138)
TITLE = (724, 52, 950, 68)
TARGET_HP = (797, 76, 945, 90)

def white(image, box, threshold=160):
    rgb = np.asarray(image.crop(box).convert('RGB'))
    return (rgb.min(2) >= threshold) & ((rgb.max(2).astype(int)-rgb.min(2)) < 65)

def trim(mask):
    yy, xx = np.where(mask)
    if not len(xx): return None
    return mask[yy.min():yy.max()+1,xx.min():xx.max()+1]

def glyphs(mask):
    occupied = mask.any(0)
    edges = np.flatnonzero(np.diff(np.r_[False, occupied, False]))
    return [(int(a),int(b),trim(mask[:,a:b])) for a,b in zip(edges[::2],edges[1::2])]

def discard_speed_padding(mask):
    """Remove detached scene components outside the anchored restart text area.

    Legal speed strokes occupy rows 4..21 and columns 28..77. A component
    touching that area remains intact, including any attached clipping/noise.
    """
    seen=np.zeros_like(mask)
    for y,x in zip(*np.where(mask)):
        if seen[y,x]:continue
        pending=[(int(y),int(x))];seen[y,x]=True;component=[];touches_text=False
        while pending:
            cy,cx=pending.pop();component.append((cy,cx))
            touches_text |= 4<=cy<22 and 28<=cx<78
            for ny in range(max(0,cy-1),min(mask.shape[0],cy+2)):
                for nx in range(max(0,cx-1),min(mask.shape[1],cx+2)):
                    if mask[ny,nx] and not seen[ny,nx]:
                        seen[ny,nx]=True;pending.append((ny,nx))
        if not touches_text:
            for cy,cx in component:mask[cy,cx]=False
    return mask

def normalize(mask):
    return np.asarray(Image.fromarray(mask.astype('uint8')*255).resize((12,10),Image.Resampling.NEAREST)) > 0

def build_templates():
    im = Image.open(ROOT/'sample.png').convert('RGB')
    templates = {str(i):[] for i in range(10)}
    # Independently annotated original screenshot HUD, not held-out live distances.
    sources = [((710,138,804,154),'1416'),((110,76,240,90),'2145/2145'),((120,97,233,112),'328/328'),
               ((799,76,943,90),'4168/4168'),((450,16,630,33),'9/30/20268:57:24')]
    for box,text in sources:
        pieces = glyphs(white(im,box))
        if len(pieces)!=len(text): raise ValueError(f'Calibration {text}: {len(pieces)} glyphs')
        for ch,(_,_,mask) in zip(text,pieces):
            if ch.isdigit(): templates[ch].append(normalize(mask).astype(int).tolist())
    if any(not v for v in templates.values()): raise ValueError('Missing calibrated digit')
    hp_templates={str(i):[] for i in range(10)}
    hp_sources=[(im,box,label) for box,label in sources[1:4]]
    for file,label in [('hp-3770-current.png','3770'),('hp-4292-current.png','4292')]:
        hp_image=Image.open(ROOT/'fixtures/calibration'/file).convert('RGB')
        hp_sources.append((hp_image,(0,0,*hp_image.size),label))
    # Independently inspected current-side glyphs from known failed preflights.
    # These complete source runs are development data, never held-out accuracy.
    for file,label in [('hp-4366-current.png','4366'),('hp-3338-current.png','3338')]:
        hp_image=Image.open(ROOT/'fixtures/calibration'/file).convert('RGB')
        hp_sources.append((hp_image,(0,0,*hp_image.size),label))
    for hp_image,box,label in hp_sources:
        pieces=glyphs(white(hp_image,box))
        if len(pieces)!=len(label):raise ValueError(f'HP calibration {label}: wrong glyph count')
        for ch,(_,_,mask) in zip(label,pieces):
            if ch.isdigit():hp_templates[ch].append(normalize(mask).astype(int).tolist())
    if any(not value for value in hp_templates.values()):raise ValueError('Missing HP font digit')
    title = trim(white(im,TITLE))
    result={'digits':templates,'hp_digits':hp_templates,'asteroid_title':title.astype(int).tolist(),
            'source':'sample.png distance/HUD calibration; HP-only current-side crops3770,4292,4366,3338. Calibration source runs excluded from accuracy holdout; see fixtures/accuracy-manifest.json'}
    (ROOT/'templates.json').write_text(json.dumps(result),encoding='utf8')
    return result

class HudReader:
    def __init__(self, profile='original', *, speed_raster='native'):
        if profile not in ('original','restart-20261001'):
            raise ValueError('Unknown HUD calibration profile')
        if speed_raster not in ('native','box_3840_to_2560') or (speed_raster!='native' and profile!='restart-20261001'):
            raise ValueError('Unsupported speed raster calibration')
        self.profile=profile
        self.speed_raster=speed_raster
        self.distance_box=DISTANCE if profile=='original' else (710,137,804,153)
        self.speed_box=SPEED if profile=='original' else (1360,1113,1440,1139)
        # Complete restart title occupies x729..824, y56..65. Crop exactly that
        # anchored text area; stars in exterior padding must not alter letter
        # geometry. The complete-letter and template checks still inspect every
        # pixel of the word, including its outermost strokes.
        self.title_box=TITLE if profile=='original' else (729,56,825,66)
        data=json.loads((ROOT/'templates.json').read_text())
        self.templates={k:[np.array(t,dtype=bool) for t in v] for k,v in data['digits'].items()}
        self.hp_templates={k:[np.array(t,dtype=bool) for t in v] for k,v in data['hp_digits'].items()}
        self.title=np.array(data['asteroid_title'],dtype=bool)
        if profile=='restart-20261001':
            calibration=json.loads((ROOT/'restart-calibration.json').read_text())
            self.title=np.array(calibration['asteroid_title'],dtype=bool)
            self.templates={k:[np.array(t,dtype=bool) for t in v] for k,v in calibration['distance_digits'].items()}
            if 'hp_digits' in calibration:
                self.hp_templates={k:[np.array(t,dtype=bool) for t in v] for k,v in calibration['hp_digits'].items()}
        self.font_banks={}
        for name,bank in [('distance',self.templates),('hp',self.hp_templates)]:
            # Deduplicate and score a complete font in one NumPy operation.
            # More observed raster variants must not add a Python loop per glyph.
            masks=[];labels=[]
            for digit,variants in bank.items():
                unique={t.tobytes():t for t in variants}
                masks.extend(unique.values());labels.extend([digit]*len(unique))
            self.font_banks[name]=(np.stack(masks),np.asarray(labels))

    def font_scores(self,g,name):
        masks,labels=self.font_banks[name]
        errors=np.mean(normalize(g)!=masks,axis=(1,2))
        return sorted((float(errors[labels==digit].min()),digit) for digit in '0123456789')

    def distance_mask(self,image):
        mask=white(image,self.distance_box)
        if self.profile=='restart-20261001':
            pieces=glyphs(mask)
            # Observed isolated stars within the first three exterior columns.
            # A 1x1 component cannot be a complete distance digit; never discard
            # a stroke that reaches another component or the calibrated core.
            if (len(pieces)>1 and pieces[0][0]<=2 and pieces[0][1]-pieces[0][0]==1 and
                    pieces[0][2].shape==(1,1) and pieces[1][0]>=6):
                mask[:,pieces[0][0]]=False
            pieces=glyphs(mask)
            # A retained 1825 m frame contains one isolated scene pixel in
            # the ordinary inter-digit gap. Only remove it between complete
            # neighbors at normal spacing; a clipped digit leaves a wider gap.
            dots=[i for i,(_,_,g) in enumerate(pieces) if g.shape==(1,1)]
            if len(dots)==1 and 0<dots[0]<len(pieces)-1:
                i=dots[0];left=pieces[i-1];right=pieces[i+1]
                if (2<=right[0]-left[1]<=8 and
                        all(9<=p[2].shape[0]<=11 and 3<=p[2].shape[1]<=14 for p in (left,right))):
                    mask[:,pieces[i][0]]=False
        # One measured bright raster/background pixel extends a complete 6 into
        # row 1. Recover only its strongly matching, fully anchored 10-row core;
        # shifted text, multiple border pixels and partial glyphs still reject.
        if not mask[0].any() and mask[1].sum()==1:
            x=int(np.flatnonzero(mask[1])[0])
            core=next((g for a,b,g in glyphs(mask[2:12]) if a<=x<b),None)
            if core is not None and core.shape[0]==10:
                scores=self.font_scores(core,'distance')
                if scores[0][0]<=.05 and scores[1][0]-scores[0][0]>=.10:
                    mask[1,x]=False
        return mask

    def distance(self,image):
        mask=self.distance_mask(image)
        pieces=glyphs(mask)
        if not 1<=len(pieces)<=6: return None,'absent_or_invalid_digit_count',0.0
        # Margin, complete height and blank vertical padding prevent silently clipped reads.
        if mask[:2].any() or mask[-2:].any() or mask[:,:2].any() or mask[:,-2:].any():
            return None,'clipped_distance',0.0
        if abs((pieces[0][0]+pieces[-1][1])/2+self.distance_box[0]-756.5)>2:
            return None,'distance_alignment_or_partial_read',0.0
        text=''; confidence=1.0
        for a,b,g in pieces:
            if g.shape[0] not in (9,10,11) or not 3<=g.shape[1]<=14:
                return None,'incomplete_distance_glyph',0.0
            scores=self.font_scores(g,'distance')
            best,ch=scores[0]; margin=scores[1][0]-best
            if best>.20 or margin<.035: return None,'ambiguous_distance_glyph',round(1-best,3)
            text+=ch; confidence=min(confidence,1-best)
        if len(text)>1 and text[0]=='0': return None,'unexpected_leading_zero',confidence
        return int(text),None,round(confidence,3)

    def speed(self,image):
        # Variable-width seven-segment display, including narrow 1 and decimal.
        # Evaluate segment interiors rather than generic OCR's disconnected components.
        a=white(image,self.speed_box)
        if self.profile=='restart-20261001':
            a=discard_speed_padding(a)
        # Translucent HUD: measured scene highlights are separate components
        # wholly left of every legal speed glyph and above the decimal.
        # Exclude that separate exterior component only; interior/decimal noise
        # and shifted/clipped digits retain all existing rejection checks.
        pieces=glyphs(a)
        for start,end,_ in pieces:
            if end<=18 and not a[20:,start:end].any():a[:,start:end]=False
        if self.profile=='restart-20261001':
            # Saved 23:46 preflight has a separate 3x4, nine-pixel scene
            # highlight in the extreme lower-left padding. No legal speed
            # glyph or decimal occupies these columns. Keep all digit-area
            # clipping/decimal checks; do not trim a component touching them.
            for start,end,component in glyphs(a):
                if (end<=5 and component.shape[0]<=4 and component.shape[1]<=3
                        and int(component.sum())<=9 and not a[:22,start:end].any()):
                    a[:,start:end]=False
        if a[:4].any() or a[22:].any() or a[:,:2].any() or a[:,-2:].any():
            return None,'clipped_or_shifted_speed',0.0
        patterns={'1110111':'0','0010010':'1','1011101':'2','1011011':'3','0111010':'4',
                  '1101011':'5','1101111':'6','1010010':'7','1111111':'8','1111011':'9'}
        # order: top, upper-left, upper-right, middle, lower-left, lower-right, bottom
        regions=[(3,0,9,2),(0,3,3,7),(9,3,12,7),(3,8,9,10),
                 (0,11,3,15),(9,11,12,15),(3,16,9,18)]
        digits=[]; confidence=1.0; decimal_at=None; previous_end=None
        for x,end,component in glyphs(a):
            # Real HUD raster phase gives a fully lit 2x2 or 2x3 bottom dot.
            # Extent alone is insufficient: require the anchored rectangle.
            decimal_shapes=((2,2),(2,3)) if self.profile=='original' else ((3,3),)
            decimal_top=20 if self.profile=='original' else 19
            if component.shape in decimal_shapes:
                if not a[decimal_top:22,x:end].all() or a[:decimal_top,x:end].any():
                    return None,'invalid_speed_decimal_position',0.0
                # Real 27.1/37.3 frames have a complete 7 ending two columns
                # before the dot. Digit/decimal shape and missing-digit guards
                # still apply; adjacent/overlapping dots remain invalid.
                if previous_end is None or not 2<=x-previous_end<=6:
                    return None,'speed_decimal_spacing_or_partial_read',0.0
                if decimal_at is not None:return None,'multiple_speed_decimals',0.0
                decimal_at=len(digits);continue
            previous_end=end
            if end-x<=4:
                # A visible 1 has no horizontal segments. Its two tapered bars
                # trim to 16 px in the actual 21.8 frame, not the 18 px of an 8.
                # Validate both fixed-position bands and their dark middle gap;
                # short/clipped/noisy strokes must not become a default 1.
                narrow=a[:,x:end]
                widths=(2,3) if self.profile=='original' else (3,)
                # Explicit 4K BOX conversion can lose one raster row at each
                # end of a narrow 1. Keep the native restart extent strict;
                # accept only complete, anchored 16-row BOX variants. The
                # Exrun 1.3 crop has a lower-bar tip at row 13, like native 1.
                variants = (((16,5,21,14),(17,5,22,14)) if self.profile=='original' else
                    ((18,4,22,13),(18,4,22,14)) +
                    (((16,5,21,14),(16,5,21,13)) if self.speed_raster=='box_3840_to_2560' else ()))
                complete=any(component.shape[0]==height and
                    not narrow[:first_row].any() and not narrow[last_row:].any() and
                    narrow[first_row:12].any(axis=1).all() and
                    not narrow[12:lower].any() and
                    narrow[lower:last_row].any(axis=1).all()
                    for height,first_row,last_row,lower in variants)
                if (end-x not in widths or not complete
                        or min(narrow[7:11].mean(),narrow[15:19].mean())<.66):
                    return None,'incomplete_or_ambiguous_speed_one',0.0
                confidence=min(confidence,float(min(narrow[7:11].mean(),narrow[15:19].mean())))
                digits.append('1');continue
            if end-x not in (10,11,12):return None,'speed_glyph_width',0.0
            g=a[4:22,x:end]
            if g.shape[1]==11:g=np.pad(g,((0,0),(0,1)))
            if g.shape[1]==10:g=np.pad(g,((0,0),(2,0)))
            fractions=[float(g[y0:y1,x0:x1].mean()) for x0,y0,x1,y1 in regions]
            if any(.20<f<.55 for f in fractions): return None,'ambiguous_speed_segment',0.0
            key=''.join('1' if f>=.55 else '0' for f in fractions)
            if key not in patterns:return None,'invalid_speed_segments',0.0
            digit=patterns[key]
            # 4 has neither top nor bottom horizontal segment. Its real lit
            # extent is rows 5..20 (16 px), or 5..21 (17 px). Decide from the
            # anchored seven-segment pattern before checking digit-specific
            # extent; a clipped 0/8/9 cannot gain a generic shorter-height pass.
            if digit=='4':
                heights=(16,17) if self.profile=='original' else (18,)
                first_row,last_row=(5,20) if self.profile=='original' else (4,21)
                native_four=(component.shape[0] in heights and not a[:first_row,x:end].any()
                    and a[first_row,x:end].any() and a[last_row,x:end].any())
                # One measured BOX-converted 4 has a single upper-left pixel
                # at row 4 and two lower-right pixels at row 20. Both side
                # bars and the middle segment must still classify as a 4.
                box_four=(self.speed_raster=='box_3840_to_2560' and
                    component.shape[0]==17 and a[4,x:end].sum()==1 and a[4,x] and
                    a[20,x:end].sum()==2 and a[20,end-2:end].all() and
                    not a[:4,x:end].any() and not a[21:,x:end].any() and
                    a[6:11,x:x+3].any(axis=1).all() and
                    a[14:20,end-3:end].any(axis=1).all())
                if not (native_four or box_four):
                    return None,'incomplete_speed_four',0.0
            elif component.shape[0] not in (17,18):
                return None,'incomplete_speed_glyph',0.0
            digits.append(digit); confidence=min(confidence,min(max(f,1-f) for f in fractions))
        if decimal_at not in (1,2) or len(digits)!=decimal_at+1:return None,'missing_or_invalid_speed_decimal',0.0
        value=float(''.join(digits[:decimal_at])+'.'+digits[-1])
        if value>44:return None,'speed_out_of_range',0.0
        return value,None,round(confidence,3)

    def hp_mask(self,image):
        mask=white(image,TARGET_HP)
        if self.profile=='restart-20261001':
            # New raster blends the thin separator with its red bar. Only this
            # calibrated separator region uses the observed chroma allowance;
            # all digit pixels keep the original white threshold. Motion JPEG
            # frames blend complete-stroke pixels down to 153. A saved JPEG
            # slash pixel has RGB255/153/173, a measured chroma spread of102.
            rgb=np.asarray(image.crop(TARGET_HP).convert('RGB'))
            separator=(rgb.min(2)>=153)&((rgb.max(2).astype(int)-rgb.min(2))<=102)
            mask[:,68:79]=separator[:,68:79]
        return mask

    def maximum_hp(self,image):
        pieces=glyphs(self.hp_mask(image))
        # Four maximum digits end before column 138 in the calibrated bar.
        # A separate world highlight beyond its right edge is not an HP glyph.
        pieces=[p for p in pieces if p[0]<139]
        # The calibrated asteroid HUD has current HP / four-digit maximum HP.
        # The slash is a separate diagonal glyph, distinct from a narrow 1.
        if not 6<=len(pieces)<=9:return None,'invalid_target_hp_format',0.0
        slash_start,slash_end,slash=pieces[-5]
        # Saved fresh frames rasterize the same visible slash as 10x6 or 9x5:
        # thresholding can lose one terminal pixel. Require its actual rising
        # diagonal, position, sparse stroke and extent rather than an exact size.
        h,w=slash.shape
        if h not in (9,10) or w not in (5,6):return None,'missing_target_hp_separator',0.0
        ys,xs=np.where(slash)
        diagonal_error=np.abs(xs/(w-1)+ys/(h-1)-1)
        start_range=(67,69) if self.profile=='original' else (70,71)
        end_range=(72,75) if self.profile=='original' else (76,76)
        if (not start_range[0]<=slash_start<=start_range[1] or not end_range[0]<=slash_end<=end_range[1]
                or not slash[0,-1] or not slash[-1,0]
                or not 9<=len(ys)<=16 or len(set(ys))<(h*3+3)//4
                or int(slash.sum(axis=1).max())>2
                or float(diagonal_error.max())>.20):
            return None,'invalid_target_hp_separator',0.0
        value='';confidence=1.0
        for _,_,g in pieces[-4:]:
            if g.shape[0] not in (9,10,11) or not 3<=g.shape[1]<=14:return None,'incomplete_maximum_hp_glyph',0.0
            scores=self.font_scores(g,'hp')
            best,digit=scores[0];margin=scores[1][0]-best
            if best>.20 or margin<.035:return None,'ambiguous_maximum_hp_glyph',round(1-best,3)
            value+=digit;confidence=min(confidence,1-best)
        if value[0]=='0':return None,'unexpected_maximum_hp_leading_zero',confidence
        return int(value),None,round(confidence,3)

    def font_alternatives(self,image,field,reason):
        """Bounded context for an LLM; never promotes an invalid control read."""
        info={'reason':reason,'for_context_only':True,'scores_are_probabilities':False,
              'geometry_complete':False,'glyphs':[],'possible_values':[]}
        ambiguous={'distance':'ambiguous_distance_glyph','target_max_hp':'ambiguous_maximum_hp_glyph'}
        if reason!=ambiguous[field]:return info
        if field=='distance':
            mask=self.distance_mask(image);pieces=glyphs(mask);bank='distance';origin=self.distance_box[0]
            if (not 1<=len(pieces)<=6 or mask[:2].any() or mask[-2:].any()
                    or mask[:,:2].any() or mask[:,-2:].any()
                    or abs((pieces[0][0]+pieces[-1][1])/2+origin-756.5)>2):return info
        else:
            # The preceding maximum_hp read verified separator geometry before
            # returning the specific font-ambiguity reason.
            pieces=[p for p in glyphs(self.hp_mask(image)) if p[0]<139][-4:]
            bank='hp';origin=TARGET_HP[0]
            if len(pieces)!=4:return info
        if any(g.shape[0] not in (9,10,11) or not 3<=g.shape[1]<=14 for _,_,g in pieces):return info
        info['geometry_complete']=True
        beam=[('',0.0,1.0)]
        for index,(start,end,g) in enumerate(pieces):
            scores=self.font_scores(g,bank)
            # Up to two reasonably matching variants, with four bounded full
            # readings overall. Pixel match scores are not calibrated odds.
            choices=[(error,digit) for error,digit in scores[:2]
                     if error<=.30 and error-scores[0][0]<=.08]
            info['glyphs'].append({'index':index,'x':origin+start,'width':end-start,
                'choices':[{'digit':digit,'match_score':round(1-error,3)} for error,digit in choices]})
            beam=sorted([(text+digit,total+error,min(confidence,1-error))
                         for text,total,confidence in beam for error,digit in choices],key=lambda x:x[1])[:4]
        info['possible_values']=[{'value':int(text),'match_score':round(1-total/len(pieces),3),
                                 'minimum_digit_match':round(confidence,3)}
                                for text,total,confidence in beam if len(text)==1 or not text.startswith('0')]
        return info

    def read(self,image,captured_at=None):
        started=time.perf_counter()
        result={'utc':datetime.now(timezone.utc).isoformat(),'distance':None,'speed':None,
                'valid':False,'reasons':[],'calibrated_size':[2560,1440], 'calibration_profile':self.profile}
        if image.size!=(2560,1440): result['reasons']=['layout_size_mismatch'];return result
        distance,de,dc=self.distance(image); speed,se,sc=self.speed(image)
        title_mask=white(image,self.title_box)
        title=trim(title_mask); asteroid=False
        title_pieces=glyphs(title_mask)
        complete_title=self.profile=='original' or (len(title_pieces)==8 and
            all(g.shape[0] in (8,9,10) and 2<=g.shape[1]<=14 for _,_,g in title_pieces))
        if complete_title and title is not None and title.shape==self.title.shape:
            asteroid=float(np.mean(title!=self.title))<.13
        result.update(distance=distance,speed=speed,asteroid_selected=asteroid,
                      distance_confidence=dc,speed_confidence=sc)
        # Visible identity guard only: maximum HP stays unchanged when G starts.
        # Includes quantized portrait and title; collisions between identical
        # looking targets remain possible, so only one miner may own targeting.
        portrait=np.asarray(image.crop((726,70,789,138)).convert('L').resize((12,12)))//32
        maximum_hp,hp_reason,hp_confidence=self.maximum_hp(image) if asteroid else (None,None,0)
        result['target_max_hp']=maximum_hp
        result['target_hp_confidence']=hp_confidence
        result['target_identity']=f'asteroid:hp:{maximum_hp}' if asteroid and maximum_hp is not None else None
        result['target_portrait']=portrait.flatten().tolist() if asteroid else None
        result['reasons']=[e for e in (de,se,hp_reason,None if asteroid else 'asteroid_title_not_verified') if e]
        uncertainty={}
        for field,reason in [('distance',de),('target_max_hp',hp_reason)]:
            if reason:uncertainty[field]=self.font_alternatives(image,field,reason)
        if se:
            uncertainty['speed']={'reason':se,'for_context_only':True,'possible_values':[],
                                  'note':'No speed alternatives asserted for a rejected segment or decimal shape.'}
        if uncertainty:result['ocr_uncertainty']=uncertainty
        if captured_at is not None:
            age=time.time()-captured_at;result['frame_age_ms']=round(age*1000,2)
            if age<0 or age>2.5:result['reasons'].append('stale_or_future_frame')
        result['valid']=not result['reasons']
        result['recognition_ms']=round((time.perf_counter()-started)*1000,3)
        return result

def main():
    p=argparse.ArgumentParser();p.add_argument('image',nargs='?',type=Path)
    p.add_argument('--calibrate',action='store_true');p.add_argument('--serve',action='store_true')
    p.add_argument('--watch-directory',type=Path);p.add_argument('--max-seconds',type=float,default=120)
    p.add_argument('--profile',choices=['original','restart-20261001'],default='original')
    args=p.parse_args()
    if args.calibrate:build_templates();print('Calibrated digit templates from sample.png');return
    reader=HudReader(args.profile)
    if args.watch_directory:
        directory=args.watch_directory;directory.mkdir(parents=True,exist_ok=True)
        deadline=time.monotonic()+args.max_seconds
        while time.monotonic()<deadline:
            if (directory/'stop').exists():break
            for request_path in directory.glob('request-*.json'):
                response=directory/request_path.name.replace('request-','response-')
                if response.exists():continue
                try:
                    request=json.loads(request_path.read_text())
                    im=Image.open(request['path']);im.load()
                    output=reader.read(im,request.get('captured_at'));output['id']=request.get('id');output['run_id']=request.get('run_id')
                except Exception as exc:output={'valid':False,'reasons':['read_error'],'error':str(exc),'id':locals().get('request',{}).get('id'),'run_id':locals().get('request',{}).get('run_id')}
                temporary=response.with_suffix('.tmp');temporary.write_text(json.dumps(output))
                temporary.replace(response)
            time.sleep(.01)
        return
    if args.serve:
        import sys
        for line in sys.stdin:
            try:
                request=json.loads(line);im=Image.open(request['path']);im.load()
                output=reader.read(im,request.get('captured_at'))
                output['id']=request.get('id')
                output['run_id']=request.get('run_id')
            except Exception as exc:output={'valid':False,'reasons':['read_error'],'error':str(exc),'id':locals().get('request',{}).get('id'),'run_id':locals().get('request',{}).get('run_id')}
            print(json.dumps(output),flush=True)
    else:print(json.dumps(reader.read(Image.open(args.image))))

if __name__=='__main__':main()
