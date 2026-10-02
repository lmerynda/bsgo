"""Pinned publisher FastVLM adapter for the offline comparison."""
import os, sys, json, time, hashlib, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
os.environ['HF_HOME']=str(ROOT/'tools/model-runtimes/hf-cache')
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TORCH_HOME']=str(ROOT/'tools/model-runtimes/torch-cache')
import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoTokenizer
spec=importlib.util.spec_from_file_location('compare_vision',Path(__file__).with_name('compare-vision.py'))
common=importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
RUN,PROMPT,save,parse,gpu=common.RUN,common.PROMPT,common.save,common.parse,common.gpu

revision='16375720c2d673fa583e57e9876afde27549c7d0'
path=ROOT/'tools/model-runtimes/hf-cache/hub/models--apple--FastVLM-0.5B/snapshots'/revision
torch.set_num_threads(4)
torch.cuda.set_device(0)
start=time.perf_counter()
model=AutoModelForCausalLM.from_pretrained(str(path),trust_remote_code=True,torch_dtype=torch.float16,
        attn_implementation='sdpa',local_files_only=True).cuda().eval()
tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True)
processor=model.get_vision_tower().image_processor
load_seconds=time.perf_counter()-start

def infer(path):
    start=time.perf_counter()
    image=Image.open(path).convert('RGB')
    width,height=image.size
    # Publisher image_aspect_ratio=pad: retain the complete screen before processing.
    side=max(width,height)
    padded=Image.new('RGB',(side,side),(0,0,0))
    padded.paste(image,((side-width)//2,(side-height)//2))
    pixels=processor.preprocess(padded,return_tensors='pt')['pixel_values'].cuda().half()
    prompt=tokenizer.apply_chat_template([{'role':'user','content':'<image>\n'+PROMPT}],tokenize=False,add_generation_prompt=True)
    chunks=prompt.split('<image>')
    ids=tokenizer(chunks[0]).input_ids+[-200]+tokenizer(chunks[1]).input_ids
    inputs=torch.tensor([ids],device='cuda')
    torch.cuda.synchronize()
    with torch.inference_mode():
        output=model.generate(inputs=inputs,images=pixels,image_sizes=[image.size],do_sample=False,
             max_new_tokens=96,repetition_penalty=1.1,pad_token_id=tokenizer.eos_token_id)
    torch.cuda.synchronize()
    text=tokenizer.decode(output[0],skip_special_tokens=True)
    return text,time.perf_counter()-start,output.shape[-1],list(pixels.shape)

infer(ROOT/'tools/hud-reader/sample.png')
rows=json.loads((RUN/'dataset.json').read_text())['rows']
results=[]
for variant in ['full','crop']:
    for row in rows:
        image_path=ROOT/row['crop_file'] if variant=='crop' else ROOT/'tools/hud-reader'/row['file']
        text,wall,tokens,shape=infer(image_path)
        parsed=parse(text) if tokens<96 else None
        score={field:('reject' if parsed is None or parsed[field] is None else
               ('correct' if abs(parsed[field]-row['expected'][field])<0.001 else 'wrong')) for field in ['distance','speed']}
        result={'id':row['id'],'source_run':row['source_run'],'variant':variant,'source':str(image_path.relative_to(ROOT)),
                'image_sha256':hashlib.sha256(image_path.read_bytes()).hexdigest(),'wall_seconds':wall,
                'expected':row['expected'],'parsed':parsed,'score':score,'done_reason':'length' if tokens>=96 else 'stop',
                'response':{'text':text,'output_tokens':tokens},'processor_tensor_shape':shape,
                'gpu_after':gpu(),'allocated_bytes':torch.cuda.memory_allocated(),'reserved_bytes':torch.cuda.memory_reserved()}
        results.append(result)
        save(RUN/'fastvlm-0.5b-results.json',{'model':'FastVLM-0.5B','revision':revision,'backend':'PyTorch CUDA FP16 SDPA',
             'torch':torch.__version__,'prompt':PROMPT,'load_seconds':load_seconds,'warmup':'unscored sample.png',
             'input_preprocessing':'publisher image processor after black square padding','results':results})
        print(json.dumps({k:result[k] for k in ['id','variant','wall_seconds','parsed','score']}),flush=True)
