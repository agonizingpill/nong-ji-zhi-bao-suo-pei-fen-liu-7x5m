from fastapi import APIRouter,Depends
from sqlalchemy import select
from .database import Session
from .security import current,allow
from .models import Claim
from .return_models import PartReturn
from .settlement_models import Settlement
from .fulfilment_schemas import ReturnIn,ReceiveIn,SettlementIn,ApprovalIn,BookingIn
from . import returns_service as returns,settlements_service as payments
from . import tenancy
router=APIRouter()
def locked(s,cls,id):
    from fastapi import HTTPException
    x=s.scalar(select(cls).where(cls.id==id).with_for_update())
    if not x:raise HTTPException(404,'返厂或结算记录不存在')
    return x
def _claim_ids(s,u):
    # 经销商账号只解析本商索赔；主管不限制
    stmt=select(Claim.id)
    if not tenancy.is_admin(u):stmt=stmt.where(Claim.dealer_id==u['dealer_id'])
    return {row for row in s.scalars(stmt)}
@router.get('/part-returns')
def list_returns(u=Depends(current)):
    with Session()as s:
        ids=_claim_ids(s,u)
        return [returns.return_view(s,x)for x in s.scalars(select(PartReturn).order_by(PartReturn.id.desc())) if x.claim_id in ids]
@router.post('/part-returns')
def dispatch(d:ReturnIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin()as s:return returns.dispatch(s,d,u)
@router.post('/part-returns/{id}/receive')
def receive(id:int,d:ReceiveIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin()as s:
        x=locked(s,PartReturn,id);tenancy.get_claim(s,x.claim_id,u)
        return returns.receive(s,x,d,u)
@router.post('/part-returns/{id}/verify')
def verify(id:int,u=Depends(current)):
    allow(u,'admin')
    with Session.begin()as s:
        x=locked(s,PartReturn,id);tenancy.get_claim(s,x.claim_id,u)
        return returns.verify(s,x,u)
@router.get('/settlements')
def list_settlements(u=Depends(current)):
    with Session()as s:
        ids=_claim_ids(s,u)
        return [payments.view(s,x)for x in s.scalars(select(Settlement).order_by(Settlement.id.desc())) if x.claim_id in ids]
@router.post('/settlements')
def create(d:SettlementIn,u=Depends(current)):
    allow(u,'admin','assessor')
    with Session.begin()as s:return payments.create(s,d,u)
@router.post('/settlements/{id}/approve')
def approve(id:int,d:ApprovalIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin()as s:
        x=locked(s,Settlement,id);tenancy.get_claim(s,x.claim_id,u)
        return payments.approve(s,x,d,u)
@router.post('/settlements/{id}/book')
def book(id:int,d:BookingIn,u=Depends(current)):
    allow(u,'admin')
    with Session.begin()as s:
        x=locked(s,Settlement,id);tenancy.get_claim(s,x.claim_id,u)
        return payments.book(s,x,d,u)
