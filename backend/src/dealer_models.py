from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class Dealer(Base):
    __tablename__='dealers'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    name:Mapped[str]=mapped_column(String(120))

class MachineHandover(Base):
    __tablename__='machine_handovers'
    id:Mapped[int]=mapped_column(primary_key=True)
    machine_id:Mapped[int]=mapped_column(ForeignKey('machines.id'))
    from_dealer_id:Mapped[int|None]=mapped_column(ForeignKey('dealers.id'),nullable=True)
    to_dealer_id:Mapped[int]=mapped_column(ForeignKey('dealers.id'))
    reason:Mapped[str]=mapped_column(Text,default='')
    by_admin:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[str]=mapped_column(String(40))
