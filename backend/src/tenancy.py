"""经销商隔离横切：所有列表过滤与归属校验统一收口在这里。

规则：
- admin（质保主管）跨经销商，可见全部，包括 dealer_id 为空的未认领旧数据；
- assessor/viewer 绑定单一经销商，只读写本商机具、索赔及证据链；
- 直接访问他商或未认领记录一律按 404 处理，不暴露存在性；
- 写操作通过行锁串行化交接与受理的并发归属竞争。
"""
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Machine,Claim,Audit
from .dealer_models import Dealer,MachineHandover

ADMIN='admin'

def is_admin(u):return u['role']==ADMIN
def dealer_id(u):return None if is_admin(u) else u.get('dealer_id')

def scope_stmt(stmt,column,u):
    """给列表查询追加经销商范围条件；主管不加限制（含未认领）。"""
    return stmt if is_admin(u) else stmt.where(column==u['dealer_id'])

def now():return datetime.now(timezone.utc).isoformat()

def audit(s,u,action,detail,dealer_id_value=None):
    did=u.get('dealer_id') if dealer_id_value is None else dealer_id_value
    s.add(Audit(actor=u['email'],action=action,detail=str(detail),at=now(),dealer_id=did))

def get_dealer(s,dealer_pk):
    d=s.get(Dealer,dealer_pk)
    if not d:raise HTTPException(404,'目标经销商不存在')
    return d

def locked_machine(s,machine_pk):
    """行锁取机具，交接/受理并发时以此串行化归属判定。"""
    m=s.scalar(select(Machine).where(Machine.id==machine_pk).with_for_update())
    if not m:raise HTTPException(404,'机具不存在')
    return m

def visible_machine(s,machine_pk,u):
    """读取场景的归属校验：他商/未认领对经销商账号返回 404。"""
    m=s.get(Machine,machine_pk)
    if not m or (not is_admin(u) and m.dealer_id!=u['dealer_id']):
        raise HTTPException(404,'机具不存在或不属于本经销商')
    return m

def locked_claim(s,claim_pk,u):
    """写场景：行锁取索赔并校验归属，越权按 404 拒绝。"""
    c=s.scalar(select(Claim).where(Claim.id==claim_pk).with_for_update())
    if not c or (not is_admin(u) and c.dealer_id!=u['dealer_id']):
        raise HTTPException(404,'索赔不存在或不属于本经销商')
    return c

def get_claim(s,claim_pk,u):
    """读场景的索赔归属校验。"""
    c=s.get(Claim,claim_pk)
    if not c or (not is_admin(u) and c.dealer_id!=u['dealer_id']):
        raise HTTPException(404,'索赔不存在或不属于本经销商')
    return c

def handover(s,machine_pk,target_dealer_id,reason,u):
    """主管交接机具：单事务内落定新归属并留痕。

    幂等：机具当前归属已是目标经销商时直接返回，不重复写交接记录；
    并发安全：调用方事务内先锁机具行，索赔受理同样锁该行，
    谁先落定归属，另一方就在锁释放后读到新归属，按新归属继续。
    """
    m=locked_machine(s,machine_pk)
    target=get_dealer(s,target_dealer_id)
    if m.dealer_id==target.id:return m,None
    previous=m.dealer_id
    m.dealer_id=target.id
    h=MachineHandover(machine_id=m.id,from_dealer_id=previous,to_dealer_id=target.id,
                      reason=(reason or '').strip(),by_admin=u['email'],created_at=now())
    s.add(h);s.flush()
    audit(s,u,'machine_handover',f'{m.serial}:{previous}->{target.id}',target.id)
    return m,h

def claim_legacy(s,u,target_dealer_id=None):
    """主管一次性认领未归属旧数据。

    未认领机具（含其历史/现场索赔）与未认领索赔全部补齐归属；
    已认领记录不动，重复调用天然幂等，不会产生第二次交接。
    """
    machines=s.scalars(select(Machine).where(Machine.dealer_id.is_(None)).with_for_update()).all()
    dealers=s.scalars(select(Dealer).order_by(Dealer.id)).all()
    if not dealers:raise HTTPException(409,'尚无经销商，无法认领旧数据')
    def pick(index):
        if target_dealer_id is not None:return get_dealer(s,target_dealer_id).id
        return dealers[index%len(dealers)].id
    machine_moves=[]
    for i,m in enumerate(machines):
        new_did=pick(i);old=m.dealer_id;m.dealer_id=new_did
        machine_moves.append((m,old,new_did))
    # 孤儿索赔（机具已被认领但索赔仍空归属，理论上由机具认领覆盖，这里兜底）
    orphan_claims=s.scalars(select(Claim).where(Claim.dealer_id.is_(None)).with_for_update()).all()
    claim_moves=[]
    for c in orphan_claims:
        m=s.get(Machine,c.machine_id)
        new_did=m.dealer_id if m and m.dealer_id is not None else get_dealer(s,target_dealer_id).id if target_dealer_id is not None else dealers[0].id
        old=c.dealer_id;c.dealer_id=new_did;claim_moves.append((c,old,new_did))
    # 未认领审计归属到目标商（主管全局动作除外：这里仅认领旧业务留痕）
    legacy_audits=s.scalars(select(Audit).where(Audit.dealer_id.is_(None)).with_for_update()).all()
    for a in legacy_audits:
        # 主管级全局动作（无具体业务归属）保持为空，仅主管可见
        if a.action in GLOBAL_AUDIT_ACTIONS:continue
        a.dealer_id=target_dealer_id if target_dealer_id is not None else dealers[0].id
    for m,old,new_did in machine_moves:
        s.add(MachineHandover(machine_id=m.id,from_dealer_id=old,to_dealer_id=new_did,
                              reason='主管一次性认领旧数据',by_admin=u['email'],created_at=now()))
    audit(s,u,'legacy_claimed',f'machines={len(machine_moves)};claims={len(claim_moves)}')
    leftover=s.scalar(select(Machine.id).where(Machine.dealer_id.is_(None)))
    return {'machines_claimed':len(machine_moves),'claims_claimed':len(claim_moves),
            'unclaimed_machines_remaining':1 if leftover is not None else 0}

# 主管全局动作不参与经销商认领，保持仅主管可见
GLOBAL_AUDIT_ACTIONS={'legacy_claimed','dealer_created','machine_handover'}

def handover_view(s,h):
    d={c.name:getattr(h,c.name) for c in h.__table__.columns}
    m=s.get(Machine,h.machine_id)
    d['machine_serial']=m.serial if m else ''
    d['from_dealer']=s.get(Dealer,h.from_dealer_id).name if h.from_dealer_id else '未认领'
    d['to_dealer']=s.get(Dealer,h.to_dealer_id).name
    return d
