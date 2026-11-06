"""Bounded public snapshot. No previous content retained on failure or expiry."""
import json,time
from pathlib import Path
import hotdeal

OUT=Path(__file__).parent/'docs/deals.json'

def build(prev, now=None):
    now=time.time() if now is None else now
    result={'items':[],'status':{},'generated':now}
    for key,(name,url,parser) in hotdeal.SITES.items():
        old=prev.get('status',{}).get(key,{})
        last=float(old.get('checked_at',0))
        if old.get('blocked'):
            result['status'][key]={**old,'name':name,'url':url}
            continue
        if now-last < hotdeal.INTERVAL:
            result['status'][key]={**old,'name':name,'url':url}
            if now-last < hotdeal.TTL:
                result['items'].extend([x for x in prev.get('items',[]) if x.get('site')==key][:hotdeal.LIMIT])
            continue
        response=hotdeal.collect_site(key)
        blocked=hotdeal.state[key]['blocked']
        result['status'][key]={'name':name,'url':url,'checked_at':now,'blocked':blocked,
                               'error':response['error'],'count':len(response['items'])}
        for item in response['items']:
            result['items'].append({**item,'site':key,'expires_at':now+hotdeal.TTL})
    return result

def main():
    try: prev=json.loads(OUT.read_text(encoding='utf-8'))
    except (OSError,ValueError): prev={}
    data=build(prev)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    for st in data['status'].values(): print(st['name'],st.get('count',0),st.get('error',''))

if __name__=='__main__': main()
