proc sql;
  create table work.sql_inner as
  select a.id,
         b.value as joined_value,
         case when a.flag = 'Y' then b.value else 0 end as category
  from work.source_a as a
       inner join work.source_b as b on a.id = b.id
  where a.flag = 'Y'
  group by a.id, b.value
  order by b.value desc;

  create table work.sql_left as
  select a.id as left_id,
         b.value as left_value
  from work.source_a as a
       left join work.source_b as b on a.id = b.id
  where value = 1;
quit;
