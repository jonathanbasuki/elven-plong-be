const apiUrl = Deno.env.get("API_URL")!;
const secret = Deno.env.get("INTERNAL_JOB_SECRET")!;
Deno.serve(async () => {
  const response = await fetch(`${apiUrl}/api/v1/dispatch/internal/process-broadcast-monitor`, {
    method: "POST", headers: { "x-internal-job-secret": secret },
  });
  return new Response(await response.text(), { status: response.status, headers: { "content-type": "application/json" } });
});
