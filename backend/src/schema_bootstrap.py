"""既有库平滑升级：create_all 不会给已存在的表补列，
这里按信息架构幂等 ALTER TABLE 追加经销商归属列。新库由 create_all 直接建出。"""
import logging
from sqlalchemy import text,inspect
log=logging.getLogger('schema-bootstrap')
_ADDITIONS={
    'users':[('dealer_id','INT NULL')],
    'machines':[('dealer_id','INT NULL')],
    'claims':[('dealer_id','INT NULL')],
    'audit':[('dealer_id','INT NULL')],
}
_FKS=[
    ('users','dealer_id','dealers'),
    ('machines','dealer_id','dealers'),
    ('claims','dealer_id','dealers'),
    ('audit','dealer_id','dealers'),
    ('machine_handovers','from_dealer_id','dealers'),
    ('machine_handovers','to_dealer_id','dealers'),
]
def ensure_schema(engine):
    insp=inspect(engine)
    existing=set(insp.get_table_names())
    dialect=engine.dialect.name
    with engine.begin() as conn:
        for table,cols in _ADDITIONS.items():
            if table not in existing:continue
            present={c['name'] for c in insp.get_columns(table)}
            for name,ddl in cols:
                if name not in present:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                    log.info('added column %s.%s',table,name)
        # SQLite 不支持 ALTER TABLE ADD CONSTRAINT；其外键在表重建时才生效，
        # 应用层已统一做归属校验。MySQL 正式库补齐真实外键约束。
        if dialect=='sqlite':return
        insp=inspect(engine)  # 重新反射，使刚追加的列对外键检查可见
        for table,col,ref in _FKS:
            if table not in existing:continue
            fks={f['constrained_columns'][0] for f in insp.get_foreign_keys(table) if f.get('constrained_columns')}
            if col in fks:continue
            present={c['name'] for c in insp.get_columns(table)}
            if col not in present:continue
            conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{col} FOREIGN KEY ({col}) REFERENCES {ref}(id)'))
            log.info('added fk %s.%s -> %s',table,col,ref)
