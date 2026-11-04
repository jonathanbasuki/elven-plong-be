# Supabase Cron Monitor

The five-minute broadcast monitor is processed by a Supabase Edge Function,
not by an in-process FastAPI timer. Add the same random value to the API `.env`
and the Edge Function secret:

```env
INTERNAL_JOB_SECRET=replace-with-a-long-random-secret
```

Deploy the function with `API_URL` set to the public HTTPS URL of this API and
`INTERNAL_JOB_SECRET` set as a function secret. The function calls:

```text
POST /api/v1/dispatch/internal/process-broadcast-monitor
```

Schedule the Edge Function once per minute from Supabase Cron. The API stores
monitor start times and count samples in PostgreSQL, so a restart does not lose
the monitoring window. Do not put the Supabase service-role key in the Edge
Function for this job.

The endpoint is idempotent enough for normal one-minute scheduling, but avoid
running multiple overlapping cron invocations until database row locking is
added.
