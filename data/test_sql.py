import duckdb

sql = '''
select count(distinct 用户id)
from '淘宝.csv'
where "商品类别"='玩具'


'''

df = duckdb.sql(sql).df()
print(df)
