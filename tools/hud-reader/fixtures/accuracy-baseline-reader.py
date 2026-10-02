"""Frozen reconstruction of the reader before the accuracy fix, for offline audit only."""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
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

def normalize(mask):
    return np.asarray(Image.fromarray(mask.astype('uint8')*255).resize((12,10),Image.Resampling.NEAREST)) > 0

class HudReader:
    def __init__(self):
        data=json.loads((ROOT/'fixtures/accuracy-baseline-templates.json').read_text())
        self.templates={k:[np.array(t,dtype=bool) for t in v] for k,v in data['digits'].items()}
        self.hp_templates={k:[np.array(t,dtype=bool) for t in v] for k,v in data['hp_digits'].items()}
        self.title=np.array(data['asteroid_title'],dtype=bool)
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
        bank=self.templates if name=='distance' else self.hp_templates
        n=normalize(g)
        return sorted((min(float(np.mean(n!=t)) for t in variants),digit) for digit,variants in bank.items())

    def distance(self,image):
        mask=white(image,DISTANCE)
        pieces=glyphs(mask)
        if not 1<=len(pieces)<=6: return None,'absent_or_invalid_digit_count',0.0
        # Margin, complete height and blank vertical padding prevent silently clipped reads.
        if mask[:2].any() or mask[-2:].any() or mask[:,:2].any() or mask[:,-2:].any():
            return None,'clipped_distance',0.0
        if abs((pieces[0][0]+pieces[-1][1])/2+DISTANCE[0]-756.5)>2:
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
        a=white(image,SPEED)
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
            if component.shape in ((2,2),(2,3)):
                if not a[20:22,x:end].all() or a[:20,x:end].any():
                    return None,'invalid_speed_decimal_position',0.0
                if previous_end is None or not 3<=x-previous_end<=6:
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
                if (end-x not in (2,3) or component.shape[0] not in (16,17)
                        or narrow[:5].any() or narrow[22:].any()
                        or narrow[12:14].any()
                        or not narrow[5:12].any(axis=1).all()
                        or not narrow[14:21].any(axis=1).all()
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
                if (component.shape[0] not in (16,17) or a[:5,x:end].any()
                        or not a[5,x:end].any() or not a[20,x:end].any()):
                    return None,'incomplete_speed_four',0.0
            elif component.shape[0] not in (17,18):
                return None,'incomplete_speed_glyph',0.0
            digits.append(digit); confidence=min(confidence,min(max(f,1-f) for f in fractions))
        if decimal_at not in (1,2) or len(digits)!=decimal_at+1:return None,'missing_or_invalid_speed_decimal',0.0
        value=float(''.join(digits[:decimal_at])+'.'+digits[-1])
        if value>44:return None,'speed_out_of_range',0.0
        return value,None,round(confidence,3)

    def maximum_hp(self,image):
        pieces=glyphs(white(image,TARGET_HP))
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
        if (not 67<=slash_start<=69 or not 72<=slash_end<=75
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

    def read(self,image,captured_at=None):
        started=time.perf_counter()
        result={'utc':datetime.now(timezone.utc).isoformat(),'distance':None,'speed':None,
                'valid':False,'reasons':[],'calibrated_size':[2560,1440]}
        if image.size!=(2560,1440): result['reasons']=['layout_size_mismatch'];return result
        distance,de,dc=self.distance(image); speed,se,sc=self.speed(image)
        title=trim(white(image,TITLE)); asteroid=False
        if title is not None and title.shape==self.title.shape:
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
        if captured_at is not None:
            age=time.time()-captured_at;result['frame_age_ms']=round(age*1000,2)
            if age<0 or age>2.5:result['reasons'].append('stale_or_future_frame')
        result['valid']=not result['reasons']
        result['recognition_ms']=round((time.perf_counter()-started)*1000,3)
        return result
