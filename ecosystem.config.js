module.exports = {
  apps: [
    {
      name: "fastapi",
      script: "/root/ai-course-platform/venv/bin/uvicorn",
      args: "app.main:app --host 127.0.0.1 --port 8000 --workers 2",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "500M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    },
    {
      name: "bot",
      script: "/root/ai-course-platform/venv/bin/python",
      args: "-m app.bot.bot",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    },
    {
      name: "celery-worker",
      script: "/root/ai-course-platform/venv/bin/celery",
      args: "-A app.celery_app worker --loglevel=info",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    },
    {
      name: "celery-beat",
      script: "/root/ai-course-platform/venv/bin/celery",
      args: "-A app.celery_app beat --loglevel=info",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    }
  ]
}
