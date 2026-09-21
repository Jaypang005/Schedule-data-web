"""CPU character TF-IDF intent model; schedule facts always come from JSON."""
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import json, math, re, unicodedata
MODEL_PATH = Path(__file__).resolve().parent / 'data' / 'language_model.json'
CANONICAL = {'first':'คาบแรก','overview':'มีเรียนอะไรบ้าง','subject':'เรียนวิชาอะไร','time':'เรียนกี่โมง','end':'เรียนถึงกี่โมง','room':'เรียนห้องไหน','students':'นักเรียนกี่คน','next':'คาบต่อไป'}
SPELLING = {'พรุ้งนี้':'พรุ่งนี้','พรุงนี้':'พรุ่งนี้','พรุ่งนี้้':'พรุ่งนี้','วนนี':'วันนี้','วันนีี':'วันนี้','วันนนี้':'วันนี้','เมือวาน':'เมื่อวาน','เมือว่าน':'เมื่อวาน','ตราราง':'ตาราง','ตาราราง':'ตาราง','ตารางเรยน':'ตารางเรียน','เรยน':'เรียน','เรีน':'เรียน','เรียยน':'เรียน','เรียร':'เรียน','คอบต่อไป':'คาบต่อไป','คาบเเรก':'คาบแรก','คาบเเรก':'คาบแรก','คาบถัดป':'คาบถัดไป','ห้องใหน':'ห้องไหน','ที่ใหน':'ที่ไหน','กี่โม้ง':'กี่โมง','กีโมง':'กี่โมง','กี่โมงง':'กี่โมง','ตอนไหนน':'ตอนไหน','วิช่า':'วิชา','วิชาา':'วิชา','สอนน':'สอน'}
OUTSIDE = re.compile(r'อาหาร|ฝน|อากาศ|ราคาทอง|หวย|ฟุตบอล|สอบ|ค่าเทอม|ทุนการศึกษา|รถรับส่ง|หนัง|ร้าน|เลิกงาน|นอน|เงินเดือน|เขียนโปรแกรม')
def correct_spelling(text):
    text=unicodedata.normalize('NFKC',text)
    for wrong,right in sorted(SPELLING.items(),key=lambda pair:len(pair[0]),reverse=True):
        text=text.replace(wrong,right)
    aliases={'จ.':'จันทร์','อ.':'อังคาร','พ.':'พุธ','พฤ.':'พฤหัสฯ','ศ.':'ศุกร์','ส.':'เสาร์','อา.':'อาทิตย์','อังคาน':'อังคาร','พุด':'พุธ','พฤหัด':'พฤหัสฯ','สุก':'ศุกร์','เสา':'เสาร์','อาทิด':'อาทิตย์','พน':'พรุ่งนี้','พน.':'พรุ่งนี้'}
    pattern=r'(?<![ก-๙A-Za-z])(?:วัน)?('+ '|'.join(re.escape(k) for k in sorted(aliases,key=len,reverse=True)) + r')(?=$|\s|นี้|หน้า|เรียน|สอน|มี|ใช้|เวลา|ห้อง|คาบ|เช้า|บ่าย|เย็น|เที่ยง|กลางคืน|ค่ำ|เริ่ม|เลิก|ต้อง|เข้า|\d)'
    text=re.sub(pattern,lambda match:aliases[match.group(1)],text)
    return text

def features_text(text):
    text=correct_spelling(text).casefold()
    text=re.sub(r'\d{5}-\d{4}|(?:สท|ทค)\s*\.?\s*\d+\s*/\s*\d+(?:-\d+)?|com\s*\d+',' ',text)
    text=re.sub(r'(?:วัน)?(?:จันทร์|อังคาร|พุธ|พฤหัส(?:บดี|ฯ)?|ศุกร์|เสาร์|อาทิตย์)|วันนี้|พรุ่งนี้|เมื่อวาน|today|tomorrow|yesterday',' ',text)
    text=re.sub(r'\d+(?:[:.]\d+)?',' ',text)
    for filler in ('รบกวน','ช่วยดูให้ที','ช่วยดู','ช่วยบอก','อยากทราบว่า','อยากรู้ว่า','ขอถามว่า','พอดีอยากรู้','ขอถามหน่อย','ช่วย','ให้หน่อย','หน่อยครับ','หน่อยค่ะ','หน่อยนะ','หน่อย','ได้ไหมครับ','ได้ไหมคะ','ครับ','ค่ะ','คะ','นะ','ทีครับ','ทีค่ะ'):
        text=text.replace(filler,'')
    return re.sub(r'\s+|[?!.,，。]','',text)

def grams(text):
    return Counter(text[i:i+n] for n in (2,3,4) for i in range(max(0,len(text)-n+1)))

def vector(text,idf):
    raw={key:(1+math.log(count))*idf.get(key,0) for key,count in grams(features_text(text)).items() if key in idf}
    norm=math.sqrt(sum(v*v for v in raw.values())) or 1
    return {key:value/norm for key,value in raw.items()}

@lru_cache(maxsize=1)
def load_model():
    if not MODEL_PATH.exists():return None
    data=json.loads(MODEL_PATH.read_text(encoding='utf-8'))
    postings=defaultdict(list)
    for index,row in enumerate(data['prototypes']):
        for key,value in row['vector'].items():postings[key].append((index,value))
    return data,postings

def predict(text):
    loaded=load_model()
    if loaded is None or OUTSIDE.search(text):return None
    data,postings=loaded
    query=vector(text,data['idf']);scores=defaultdict(float)
    for key,value in query.items():
        for index,weight in postings.get(key,()):scores[index]+=value*weight
    labels={}
    for index,score in scores.items():
        label=data['prototypes'][index]['intent']
        labels[label]=max(labels.get(label,0),score)
    ranked=sorted(labels.items(),key=lambda pair:pair[1],reverse=True)
    if not ranked:return None
    label,score=ranked[0];runner=ranked[1][1] if len(ranked)>1 else 0
    if label=='reject' or score<data['threshold'] or score-runner<data['margin']:return None
    return {'intent':label,'score':round(score,4),'margin':round(score-runner,4),'canonical':CANONICAL[label]}

def enhance(question,parsed):
    if parsed.get('query_mode') not in (None,'schedule_detail') or parsed.get('_next_period') or parsed.get('_first_period') or parsed.get('_info_key') or parsed.get('_yes_no'):
        return None
    asks=set(parsed.get('asks',[]))
    next_words=bool(re.search(r'ต่อ|ถัด|คาบหน้า|หลังจากนี้',question))
    first_words=bool(re.search(r'คาบ\s*(?:แรก|ที่\s*1|หนึ่ง)',question))
    end_words=bool(re.search(r'เลิก|เสร็จ|จบ|หมดคาบ|หมดเวลา',question))
    if asks and not (next_words or end_words or first_words):return None
    candidate=predict(question)
    if candidate is None:return None
    if asks:
        refine_first=first_words and candidate['intent']=='first'
        refine_next=next_words and candidate['intent']=='next' and asks <= {'subject','time','room','class'}
        refine_end=end_words and candidate['intent']=='end' and asks <= {'time'} and not parsed.get('_asks_end')
        if not (refine_first or refine_next or refine_end):return None
    if not (parsed.get('day') or parsed.get('class') or re.search(r'วันนี้|พรุ่งนี้|เมื่อวาน|เรียน|สอน|คาบ|วิชา|นักเรียน|ตาราง|ห้อง',question)):
        return None
    return question+' '+candidate['canonical']
