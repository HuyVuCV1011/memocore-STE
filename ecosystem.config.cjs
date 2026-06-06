module.exports = {
  apps: [
    {
      name: "memocore-ste",
      cwd: __dirname,
      script: ".venv\\Scripts\\python.exe",
      args: "-m memocore.cli.main run --provider groq",
      interpreter: "none",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
