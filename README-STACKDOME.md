# Stackdome deployment

Deploy the panel service with these values in the deploy form:

- **دایرکتوری ساخت (build context)**: `.` (ریشه مخزن — چون همه فایل‌ها فلت هستن)
- **مسیر داکرفایل**: `Dockerfile`
- **پورت**: `8000`
- **افشاگری عمومی**: روشن (اگر می‌خواید پنل از بیرون در دسترس باشه)

A real VPS host requires KVM/libvirt and `/dev/kvm` access, which Stackdome's
containers don't provide. Run `install-agent.sh` on a separate real Linux
host with KVM, then set the panel's `AGENT_URL` / `AGENT_TOKEN` env vars to
point at that host. See `README.md` for the full variable list.
