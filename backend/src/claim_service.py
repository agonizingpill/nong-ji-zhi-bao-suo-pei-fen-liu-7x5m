from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Machine,Claim,Inspection,Audit
from . import tenancy

def obj(x):return {c.name:getattr(x,c.name) for c in x.__table__.columns}
def get(s,model,id):
    x=s.get(model,id)
    if not x:raise HTTPException(404,'记录不存在')
    return x
def audit(s,u,action,detail,dealer_id_value=None):tenancy.audit(s,u,action,detail,dealer_id_value)
def claim_view(s,c):
    d=obj(c);m=s.get(Machine,c.machine_id)
    d['machine_serial']=m.serial if m else ''
    d['dealer_id']=c.dealer_id
    i=s.scalar(select(Inspection).where(Inspection.claim_id==c.id));d['inspection']=obj(i) if i else None
    return d
def submit(s,data,u):
    # 锁机具行：与主管交接互斥，先落定的归属决定本笔索赔归属
    m=tenancy.locked_machine(s,data.machine_id)
    if not tenancy.is_admin(u) and m.dealer_id!=u['dealer_id']:
        raise HTTPException(404,'机具不存在或不属于本经销商，不能受理索赔')
    if m.dealer_id is None:
        raise HTTPException(409,'该机具尚未由主管认领，暂不能受理索赔')
    if data.hours_at_failure<m.hours:raise HTTPException(409,'故障工时不能小于机具最新台账工时')
    # 归属快照：交接只改变机具当前归属，不改变已受理索赔的归属
    c=Claim(**data.model_dump(),dealer_id=m.dealer_id,historical=False,
            status='out_of_warranty' if data.hours_at_failure>m.warranty_hours else 'submitted',
            assessment_note='超出质保工时' if data.hours_at_failure>m.warranty_hours else '')
    m.hours=data.hours_at_failure;s.add(c);s.flush();audit(s,u,'claim_received',c.code,c.dealer_id);return claim_view(s,c)
def inspect(s,c,data,u):
    if c.status!='assessed':raise HTTPException(409,'仅完成风险分流的索赔可补拆检')
    i=s.scalar(select(Inspection).where(Inspection.claim_id==c.id))
    if not i:i=Inspection(claim_id=c.id,decision='pending',**data.model_dump());s.add(i)
    else:i.findings=data.findings;i.evidence_complete=data.evidence_complete
    audit(s,u,'inspection_updated',c.code,c.dealer_id);s.flush();return obj(i)
def adjudicate(s,c,decision,u):
    if c.status=='out_of_warranty':raise HTTPException(409,'机具故障工时已过保，不允许质保赔付')
    if c.status!='assessed':raise HTTPException(409,'索赔须完成风险分流，且不可重复裁决')
    i=s.scalar(select(Inspection).where(Inspection.claim_id==c.id))
    if not i:raise HTTPException(409,'必须先记录拆检意见')
    if decision=='paid' and c.risk is not None and c.risk>=.5 and not i.evidence_complete:raise HTTPException(409,'高争议风险索赔必须补齐拆检证据后才能赔付')
    c.status=decision;i.decision=decision;audit(s,u,'claim_adjudicated',f'{c.code}:{decision}',c.dealer_id);return claim_view(s,c)
