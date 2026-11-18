import os,hmac,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI,Depends,HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError
from redis import Redis
from .database import Base,engine,Session
from .models import User,Machine,Claim,Inspection,Audit
from .dealer_models import Dealer,MachineHandover
from .security import current,allow,password_hash,token_for
from .schemas import Login,MachineIn,ClaimIn,InspectionIn,DecisionIn,DealerIn,HandoverIn,ClaimLegacyIn
from .claim_service import obj,audit,claim_view,submit,inspect,adjudicate
from . import tenancy
from .middlewares import RequestTrace,Recovery
from .fulfilment_routes import router as fulfilment_router
from .workshop_routes import router as workshop_router
from .schema_bootstrap import ensure_schema
from scripts.seed_demo import seed
logging.basicConfig(level=logging.INFO)
r=Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    if os.getenv('SEED_DEMO','true').lower()=='true':
        try:seed()
        except Exception:logging.exception('seed failed')
    yield
app=FastAPI(title='耕川 · 质保田间档案',lifespan=lifespan)
app.add_middleware(RequestTrace);app.add_middleware(Recovery)
app.include_router(fulfilment_router)
app.include_router(workshop_router)
@app.exception_handler(IntegrityError)
async def duplicate(request,e):return JSONResponse({'detail':'编号已存在或关联数据不合法'},status_code=409)
@app.get('/health')
def health():
    with Session() as s:s.execute(text('SELECT 1'))
    return {'status':'ok','redis':r.ping()}
@app.post('/auth/login')
def login(data:Login):
    with Session() as s:
        u=s.scalar(select(User).where(User.email==data.email))
        if not u or not hmac.compare_digest(u.password,password_hash(data.password)):raise HTTPException(401,'账号或密码不正确')
        dealer=s.get(Dealer,u.dealer_id) if u.dealer_id else None
        return {'token':token_for(u),'role':u.role,'email':u.email,
                'dealer_id':u.dealer_id,'dealer_name':dealer.name if dealer else None}
@app.get('/dealers')
def dealers(u=Depends(current)):
    # 经销商目录本身不涉密：前端表单需要经销商名称；业务数据仍逐行隔离
    with Session() as s:return [obj(x) for x in s.scalars(select(Dealer).order_by(Dealer.id))]
@app.post('/dealers')
def add_dealer(d:DealerIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin() as s:
        x=Dealer(**d.model_dump());s.add(x);s.flush();tenancy.audit(s,u,'dealer_created',f'{x.code}:{x.name}');return obj(x)
@app.get('/machines')
def machines(u=Depends(current)):
    with Session() as s:
        stmt=tenancy.scope_stmt(select(Machine).order_by(Machine.id),Machine.dealer_id,u)
        rows=[]
        for m in s.scalars(stmt):
            d=obj(m);d['dealer_name']=s.get(Dealer,m.dealer_id).name if m.dealer_id else None;rows.append(d)
        return rows
@app.post('/machines')
def add_machine(data:MachineIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin() as s:
        did=u['dealer_id']
        if tenancy.is_admin(u):
            did=data.dealer_id
            if did is None:raise HTTPException(409,'主管登记机具须指定归属经销商')
            tenancy.get_dealer(s,did)
        x=Machine(**data.model_dump(exclude={'dealer_id'}),dealer_id=did)
        s.add(x);s.flush();audit(s,u,'machine_registered',x.serial,did);return obj(x)
@app.put('/machines/{id}')
def update_machine(id:int,data:MachineIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin() as s:
        m=tenancy.locked_machine(s,id)
        if data.hours<m.hours:raise HTTPException(409,'机具累计工时不可倒退')
        for k,v in data.model_dump(exclude={'dealer_id'}).items():setattr(m,k,v)
        audit(s,u,'machine_updated',m.serial,m.dealer_id);return obj(m)
@app.get('/machines/{id}/handovers')
def machine_handovers(id:int,u=Depends(current)):
    with Session() as s:
        m=tenancy.visible_machine(s,id,u)
        stmt=select(MachineHandover).where(MachineHandover.machine_id==id).order_by(MachineHandover.id)
        if not tenancy.is_admin(u):
            # 经销商只能看与本商相关的交接（转入或转出）
            stmt=stmt.where((MachineHandover.from_dealer_id==u['dealer_id'])|(MachineHandover.to_dealer_id==u['dealer_id']))
        return [tenancy.handover_view(s,h) for h in s.scalars(stmt)]
@app.post('/machines/{id}/handover')
def handover_machine(id:int,d:HandoverIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin() as s:
        m,h=tenancy.handover(s,id,d.target_dealer_id,d.reason,u)
        result=obj(m)
        if h:result['handover_id']=h.id
        result['idempotent']=h is None
        return result
@app.post('/legacy/claim')
def claim_legacy(d:ClaimLegacyIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin() as s:return tenancy.claim_legacy(s,u,d.dealer_id)
@app.get('/claims')
def claims(u=Depends(current)):
    with Session() as s:
        stmt=select(Claim).where(Claim.historical==False).order_by(Claim.id.desc())
        stmt=tenancy.scope_stmt(stmt,Claim.dealer_id,u)
        out=[]
        for c in s.scalars(stmt):
            d=claim_view(s,c)
            d['dealer_name']=s.get(Dealer,c.dealer_id).name if c.dealer_id else None
            out.append(d)
        return out
@app.post('/claims')
def add_claim(data:ClaimIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin() as s:return submit(s,data,u)
@app.post('/claims/{id}/assess')
def assess_claim(id:int,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin() as s:
        c=tenancy.locked_claim(s,id,u)
        if c.historical or c.status not in ['submitted','failed']:raise HTTPException(409,'当前索赔状态不可分流')
        if c.hours_at_failure>s.get(Machine,c.machine_id).warranty_hours:
            c.status='out_of_warranty';c.assessment_note='超出质保工时';audit(s,u,'warranty_rejected',c.code,c.dealer_id)
        else:c.status='queued';audit(s,u,'risk_queued',c.code,c.dealer_id)
        result=claim_view(s,c)
    if result['status']=='queued':r.rpush('claim-risk',id)
    return result
@app.put('/claims/{id}/inspection')
def save_inspection(id:int,data:InspectionIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin() as s:return inspect(s,tenancy.locked_claim(s,id,u),data,u)
@app.post('/claims/{id}/decision')
def decide(id:int,data:DecisionIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin() as s:return adjudicate(s,tenancy.locked_claim(s,id,u),data.decision,u)
@app.get('/audit')
def audit_log(u=Depends(current)):
    with Session() as s:
        stmt=select(Audit).order_by(Audit.id.desc())
        stmt=tenancy.scope_stmt(stmt,Audit.dealer_id,u)
        return [obj(x) for x in s.scalars(stmt.limit(40))]
