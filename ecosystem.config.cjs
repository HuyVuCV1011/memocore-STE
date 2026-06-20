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
      },
    },
  ],
};
