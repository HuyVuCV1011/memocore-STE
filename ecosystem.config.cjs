module.exports = {
  apps: [
    {
      name: "memocore-ste",
      cwd: __dirname,
      script: ".venv\\Scripts\\memocore.exe",
      args: "run",
      interpreter: "none",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
