# Broadcast monitoring

Monitoring state and count samples are stored in Postgres, so API restarts do not lose the five-minute window. Set `INTERNAL_JOB_SECRET` on the API and Edge Function, plus `API_URL`. The function never uses a Supabase service-role key.

Deploy the function and schedule it every minute with pg_cron/pg_net:

```sql
select cron.schedule('process-broadcast-monitor', '* * * * *', $$
  select net.http_post(url := 'https://PROJECT.supabase.co/functions/v1/process-broadcast-monitor', body := '{}'::jsonb);
$$);
```

Apply migrations first. After snapshots are best effort and do not affect status processing.
