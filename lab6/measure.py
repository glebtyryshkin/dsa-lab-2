import asyncio
from sqlalchemy import event
from app import db
from app.schema import schema, context_getter

QUERY = '''query A($status: OrderStatus) {
  orders(status: $status) {
    id status total customerName
    restaurant { name }
    items { quantity price dish { name } }
  }
}'''

async def main():
    for status in ('CANCELLED', 'READY', None):
        for naive in (True, False):
            statements = []
            def collect(conn, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith('SELECT'):
                    statements.append(statement)
            event.listen(db.engine.sync_engine, 'before_cursor_execute', collect)
            context = context_getter()
            context['naive'] = naive
            try:
                result = await schema.execute(QUERY, variable_values={'status': status}, context_value=context)
            finally:
                event.remove(db.engine.sync_engine, 'before_cursor_execute', collect)
            if result.errors:
                raise RuntimeError(result.errors)
            print(f"RESULT status={status or 'ALL'} orders={len(result.data['orders'])} naive={naive} selects={len(statements)}", flush=True)
    await db.engine.dispose()

if __name__ == '__main__': asyncio.run(main())
