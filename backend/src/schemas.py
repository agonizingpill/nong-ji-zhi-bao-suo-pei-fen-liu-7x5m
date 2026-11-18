from pydantic import BaseModel,Field
from typing import Literal
class Login(BaseModel):
    email:str
    password:str
class DealerIn(BaseModel):
    code:str=Field(min_length=2,max_length=40)
    name:str=Field(min_length=2,max_length=120)
class MachineIn(BaseModel):
    serial:str=Field(min_length=2,max_length=60)
    model:str=Field(min_length=2,max_length=60)
    purchase_date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    warranty_hours:int=Field(gt=0,le=10000)
    hours:int=Field(ge=0,le=100000)
    # 仅主管登记时生效；经销商账号忽略此值并强制为本商
    dealer_id:int|None=None
class ClaimIn(BaseModel):
    code:str=Field(min_length=2,max_length=60)
    machine_id:int
    part:Literal['hydraulic','engine','transmission']
    hours_at_failure:int=Field(ge=0,le=100000)
    age_days:int=Field(ge=0,le=20000)
    leak_flag:bool=False
class InspectionIn(BaseModel):
    findings:str=Field(min_length=8,max_length=5000)
    evidence_complete:bool
class DecisionIn(BaseModel):
    decision:Literal['paid','denied']
class HandoverIn(BaseModel):
    target_dealer_id:int
    reason:str=Field(default='',max_length=2000)
class ClaimLegacyIn(BaseModel):
    # 为空时在全部经销商间轮询分派，保证一次认领覆盖所有未归属记录
    dealer_id:int|None=None
