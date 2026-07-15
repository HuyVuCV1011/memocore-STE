module.exports = {
  apps: [
    {
      name: "memocore-ste",
      cwd: __dirname,
      script: ".venv\\Scripts\\python.exe",
      args: "-m memocore.cli.main run --provider groq",
      interpreter: "none",
      min_uptime: "10s",
      max_restarts: 5,
      restart_delay: 5000,
      exp_backoff_restart_delay: 2000,
      env: {
        PYTHONUNBUFFERED: "1",
        MEMOCORE_DEPLOY_COMMIT: process.env.MEMOCORE_DEPLOY_COMMIT || "unknown",
        MEMOCORE_DEPLOY_DIRTY: process.env.MEMOCORE_DEPLOY_DIRTY || "unknown",
        MEMOCORE_DEPLOY_SCHEMA: process.env.MEMOCORE_DEPLOY_SCHEMA || "unknown",
        MEMOCORE_DEPLOYED_AT: process.env.MEMOCORE_DEPLOYED_AT || "unknown",
      },
    },
  ],
};
