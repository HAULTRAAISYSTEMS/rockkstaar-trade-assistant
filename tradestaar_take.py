"""Tradestaar Take Phase 5.

AI is an analysis assistant, never a source of financial facts. The adapter only
receives admin-supplied/verified facts and must cite those source IDs. Generated
content is persisted through create_draft() with take_origin='ai'; this module
has no publication, realtime-announcement, or notification capability.
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from urllib import request
import research_feed as rf

class TakeProviderError(RuntimeError): pass
class TakeDataError(ValueError): pass

@dataclass(frozen=True)
class VerifiedSource:
    id: str
    label: str
    url: str
    facts: tuple[str, ...]

def _clean_source(raw, i):
    if not isinstance(raw, dict): raise TakeDataError('source must be an object')
    sid=str(raw.get('id') or f'source-{i+1}').strip(); label=str(raw.get('label') or '').strip(); url=str(raw.get('url') or '').strip()
    facts=tuple(str(x).strip() for x in (raw.get('facts') or []) if str(x).strip())
    if not label or not facts: raise TakeDataError('each source requires a label and verified facts')
    if url: rf.validate_source_url(url)
    return VerifiedSource(sid,label,url,facts)

def build_verified_context(data):
    if not isinstance(data,dict): raise TakeDataError('verified research data is required')
    ticker=rf.normalize_ticker(data.get('ticker')); company=str(data.get('company_name') or '').strip()
    if not company: raise TakeDataError('company_name is required')
    sources=tuple(_clean_source(x,i) for i,x in enumerate(data.get('sources') or []))
    if not sources: raise TakeDataError('at least one verified source is required')
    metrics=[]
    for i,m in enumerate(data.get('metrics') or []): metrics.append(rf.validate_metric(m,i))
    return {'ticker':ticker,'company_name':company,'category':rf._choice(data.get('category') or 'Breaking News',rf.CATEGORIES,'category'),'sentiment':rf._choice(data.get('sentiment') or 'Neutral',rf.SENTIMENTS,'sentiment'),'headline':str(data.get('headline') or '').strip(),'research_notes':str(data.get('research_notes') or '').strip(),'metrics':metrics,'sources':sources}

class TakeProvider:
    name='base'
    def generate(self,context): raise NotImplementedError

class OpenAICompatibleTakeProvider(TakeProvider):
    """Small HTTP adapter compatible with providers exposing /v1/chat/completions."""
    name='openai-compatible'
    def __init__(self,api_key=None,model=None,base_url=None,timeout=20):
        self.api_key=api_key or os.getenv('TRADESTAAR_TAKE_API_KEY'); self.model=model or os.getenv('TRADESTAAR_TAKE_MODEL','gpt-5-mini'); self.base_url=(base_url or os.getenv('TRADESTAAR_TAKE_BASE_URL','https://api.openai.com/v1')).rstrip('/'); self.timeout=timeout
        if not self.api_key: raise TakeProviderError('TRADESTAAR_TAKE_API_KEY is not configured')
    def generate(self,context):
        source_text='\n'.join(f"[{s.id}] {s.label}: "+' | '.join(s.facts) for s in context['sources'])
        metric_text='\n'.join(json.dumps(m,sort_keys=True) for m in context['metrics']) or 'No structured metrics supplied.'
        prompt=f"""Write a concise Tradestaar Take using ONLY the verified facts below. Never invent, infer, calculate, or alter financial numbers, estimates, guidance, SEC facts, analyst facts, dates, or sources. If evidence is insufficient, say so. Return JSON with keys take (string) and source_ids (array). Every factual claim must be supported by listed source IDs.\nTicker: {context['ticker']}\nCompany: {context['company_name']}\nHeadline: {context['headline']}\nResearch notes: {context['research_notes']}\nMetrics: {metric_text}\nVerified sources:\n{source_text}"""
        body=json.dumps({'model':self.model,'messages':[{'role':'system','content':'You summarize verified market research. Never create facts or investment guarantees.'},{'role':'user','content':prompt}],'response_format':{'type':'json_object'}}).encode()
        req=request.Request(self.base_url+'/chat/completions',data=body,headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'})
        try:
            with request.urlopen(req,timeout=self.timeout) as r: payload=json.loads(r.read().decode())
            result=json.loads(payload['choices'][0]['message']['content'])
        except Exception as e: raise TakeProviderError('Tradestaar Take provider request failed') from e
        take=str(result.get('take') or '').strip(); cited=[str(x) for x in result.get('source_ids') or []]; allowed={s.id for s in context['sources']}
        if not take: raise TakeProviderError('provider returned an empty Tradestaar Take')
        if not cited or any(x not in allowed for x in cited): raise TakeProviderError('provider returned invalid source attribution')
        return {'take':take,'source_ids':cited,'provider':self.name,'model':self.model}

def generate_take_draft(data, actor, provider, conn=None):
    """Generate and save an AI Take as a DRAFT only. There is intentionally no publish flag."""
    rf._assert_admin(actor); context=build_verified_context(data); result=provider.generate(context)
    cited=set(result['source_ids']); sources=[s for s in context['sources'] if s.id in cited]
    source_label='; '.join(s.label for s in sources); source_urls=[s.url for s in sources if s.url]
    draft={'ticker':context['ticker'],'company_name':context['company_name'],'headline':context['headline'] or f"{context['ticker']} research update",'research_notes':context['research_notes'] or 'AI-assisted Tradestaar Take generated from admin-verified research data.','category':context['category'],'sentiment':context['sentiment'],'source_name':source_label,'source_url':source_urls[0] if len(source_urls)==1 else None,'tradestaar_take':result['take'],'take_origin':'ai','should_notify':False}
    post_id=rf.create_draft(draft,actor,metrics=context['metrics'],conn=conn)
    return {'post_id':post_id,'status':'draft','take':result['take'],'source_ids':result['source_ids'],'sources':[{'id':s.id,'label':s.label,'url':s.url,'facts':list(s.facts)} for s in sources],'provider':result.get('provider'),'model':result.get('model')}
