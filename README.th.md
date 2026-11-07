# ระบบจัดเก็บไฟล์หลายองค์กร (Multi-tenant file storage) — PoC

[English](README.md) · ภาษาไทย

Django Ninja + Postgres + SeaweedFS + แอป React ทั้งหมดรันบน docker compose ดูศัพท์เฉพาะใน `CONTEXT.md` และการตัดสินใจเชิงออกแบบใน `docs/adr/`

## วิธีรัน

รันได้สองแบบ ทั้งสองแบบเริ่มด้วยการตั้งค่าครั้งแรกเหมือนกัน คือไฟล์ `.env` ที่มี secret และไฟล์ master key

**ข้ามบล็อกนี้ถ้ามี `.env` และ `~/.filestore/master.key` อยู่แล้ว** (เช่นบนเครื่องที่เคยรัน stack มาก่อน) เพราะการเปลี่ยนสองไฟล์นี้ทีหลังทำให้ข้อมูลของเครื่องนั้นเสีย: master key ใหม่ทำให้ไฟล์ที่เก็บไว้ทั้งหมดอ่านไม่ได้ตลอดไป และรหัสผ่านฐานข้อมูลใหม่จะไม่ตรงกับ volume ฐานข้อมูลเดิม (role ถูกสร้างครั้งแรกครั้งเดียวเท่านั้น) คุณจึงต้อง `docker compose down -v` ซึ่งลบข้อมูลทั้งหมด คำสั่งด้านล่างเขียนให้ไม่แตะไฟล์ที่มีอยู่แล้ว

```sh
if [ ! -f .env ]; then
  cp .env.example .env && chmod 660 .env
  # Put a random secret in every empty value (macOS; on Linux remove the '' after -i):
  for k in DJANGO_SECRET_KEY POSTGRES_PASSWORD S3_ACCESS_KEY S3_SECRET_KEY S3_ADMIN_ACCESS_KEY S3_ADMIN_SECRET_KEY \
           SEAWEED_ADMIN_PASSWORD DB_OWNER_PASSWORD DB_APP_PASSWORD DB_BACKOFFICE_PASSWORD BACKOFFICE_SECRET_KEY; do
    sed -i '' "s|^$k=.*|$k=$(openssl rand -hex 24)|" .env
  done
  # The master key is a file outside this repo. MASTER_KEY_HOST_FILE must be an absolute path.
  sed -i '' "s|^MASTER_KEY_HOST_FILE=.*|MASTER_KEY_HOST_FILE=$HOME/.filestore/master.key|" .env
fi
mkdir -p ~/.filestore
if [ ! -f ~/.filestore/master.key ]; then   # keep a safe copy of this file: without it no stored file can be read again
  openssl rand -base64 32 > ~/.filestore/master.key && chmod 600 ~/.filestore/master.key
fi
```

คำอธิบาย: บนเครื่อง macOS ใช้ `sed -i ''` ส่วนบน Linux ให้ตัด `''` ออก master key เป็นไฟล์นอก repo `MASTER_KEY_HOST_FILE` ต้องเป็น path เต็ม และควรเก็บสำเนาไฟล์ master key ไว้ที่ปลอดภัย ถ้าหายจะอ่านไฟล์ที่เก็บไว้ไม่ได้อีก

### A. รันทุกอย่างด้วย Docker Compose

```sh
docker compose up -d --build --wait    # the first run takes a few minutes: it builds the images and pulls SeaweedFS (about 1 GB)

# Nothing exists yet: create an organization, a person in it, and a Platform operator for the back office.
docker compose run --rm ops create_tenant "Acme"
docker compose run --rm ops create_user alice@example.com 'a-password'
docker compose run --rm ops add_membership alice@example.com "Acme" admin
docker compose run --rm ops create_user --operator you@example.com 'another-password'
```

การรันครั้งแรกใช้เวลาสองสามนาที เพราะต้อง build image และดึง SeaweedFS (ประมาณ 1 GB) ตอนเริ่มยังไม่มีข้อมูลใดเลย ห้าคำสั่งหลังสร้างองค์กร ผู้ใช้ในองค์กรนั้น และ Platform operator สำหรับหลังบ้าน

เปิดที่อยู่เหล่านี้ได้ ทั้งหมดเข้าได้จากเครื่องตัวเองเท่านั้น (loopback):

| ที่อยู่ | คืออะไร |
|---|---|
| http://127.0.0.1:5173 | แอป: ล็อกอินด้วย `alice@example.com` |
| http://127.0.0.1:8001/admin/ | หลังบ้าน (back office): ล็อกอินด้วย operator |
| http://127.0.0.1:12535 | SeaweedFS admin: user `admin` รหัสผ่านคือ `SEAWEED_ADMIN_PASSWORD` ใน `.env` |
| http://127.0.0.1:8000/api/health | health check ของ API |

Postgres, S3, filer และ master อยู่ในเครือข่ายของ compose เท่านั้น มีสามเครือข่าย (`db`, `storage`, `web`) ดังนั้นหลังบ้านและแอป React จึงไม่มีเส้นทางเครือข่ายไปหา SeaweedFS ซึ่ง filer ของมันรับคำขอโดยไม่ต้องยืนยันตัวตน

คำสั่งที่ใช้บ่อย:

```sh
docker compose ps                  # what is running
docker compose logs -f backend     # follow one service: backend, backoffice, seaweedfs, maintenance, ...
docker compose stop                # stop everything, keep the data
docker compose down                # remove the containers, keep the data (the volumes)
docker compose down -v             # also delete the data (database and files): start again from nothing
COMPOSE_PROFILES=backup docker compose up -d --build --wait   # also run the Backup services (see Backup and restore)
```

- `ps` ดูว่าอะไรกำลังรัน
- `logs -f backend` ติดตาม log ของ service เดียว (หรือ `backoffice`, `seaweedfs`, `maintenance` ฯลฯ)
- `stop` หยุดทุกอย่าง เก็บข้อมูลไว้
- `down` ลบ container เก็บข้อมูลไว้ (volume)
- `down -v` ลบข้อมูลด้วย (ฐานข้อมูลและไฟล์) เริ่มใหม่จากศูนย์
- `COMPOSE_PROFILES=backup ...` เปิดบริการ Backup ด้วย (ดูหัวข้อ "Backup และ restore")

### B. รัน backend กับ frontend บนเครื่อง ส่วนที่เหลืออยู่ใน Docker

Postgres และ SeaweedFS ยังอยู่ใน Docker พอร์ตของทั้งสองปิดไว้เป็นค่าเริ่มต้น ไฟล์ `compose.dev.yaml` จึงเปิดเฉพาะสองพอร์ตนั้นบน loopback ต้องมี `uv` (ดึง Python ให้เอง) และ Node 20 ขึ้นไป

```sh
export COMPOSE_FILE=compose.yaml:compose.dev.yaml    # in every terminal that runs `docker compose` below
docker compose up -d --wait postgres seaweedfs storage-init migrate    # database, storage, the bucket, the schema
```

ต้อง `export COMPOSE_FILE` ในทุก terminal ที่เรียก `docker compose` ต่อไปนี้ คำสั่งที่สองเริ่มฐานข้อมูล storage สร้าง bucket และสร้าง schema

Terminal 1 — backend (ห้ามเปิด container `backend` พร้อมกัน เพราะทั้งคู่ต้องใช้พอร์ต 8000):

```sh
set -a; . ./.env; set +a
cd backend
POSTGRES_DB=filestore POSTGRES_USER=filestore_app POSTGRES_PASSWORD="$DB_APP_PASSWORD" POSTGRES_HOST=127.0.0.1 \
S3_ENDPOINT_URL=http://127.0.0.1:8333 S3_BUCKET=files MASTER_KEY_FILE="$MASTER_KEY_HOST_FILE" \
uv run --python 3.12 --no-project --with-requirements requirements.txt python manage.py runserver 127.0.0.1:8000
```

Terminal 2 — frontend (ห้ามเปิด container `frontend` พร้อมกัน เพราะทั้งคู่ต้องใช้พอร์ต 5173):

```sh
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173, calls the API at http://127.0.0.1:8000
```

สร้างองค์กรและผู้ใช้คนแรกด้วยคำสั่ง `docker compose run --rm ops ...` แบบเดียวกับข้อ A (ใช้ฐานข้อมูลเดียวกัน) เปิดแอปผ่าน `127.0.0.1` หรือ `localhost` พอร์ต 5173 เท่านั้น เพราะเป็นสอง origin ที่ API อนุญาต (`CORS_ALLOWED_ORIGINS`) โหมดนี้ไม่ได้เริ่มหลังบ้าน ถ้าต้องการให้เพิ่ม `backoffice` ต่อท้ายคำสั่ง `up` อย่ารัน `scripts/test.sh` ขณะใช้ `compose.dev.yaml` เพราะมีการตรวจข้อหนึ่งที่ยืนยันว่า Postgres และ S3 ไม่ได้เปิดพอร์ต

การหยุด: กด Ctrl-C ทั้งสอง terminal แล้ว `docker compose down` (หรือ `stop`)

## Master key

master key ใช้ห่อ Tenant key ทุกตัว (ADR 0001) มันเป็นไฟล์ที่เก็บ **นอก repo และนอกทุก volume และ Backup** ตัวแปร `MASTER_KEY_HOST_FILE` ใน `.env` คือ path เต็มของไฟล์บนเครื่องนี้ compose เมานต์ให้เฉพาะ backend ในรูปของ secret ถ้าทำหาย ไฟล์ที่เก็บไว้ทุกไฟล์จะอ่านไม่ได้

## API

ทุกจุดต้องมี bearer token (`POST /api/auth/login`) และต้องมี `X-Tenant-ID` ยกเว้นรายการองค์กร

| | |
|---|---|
| `GET /api/tenants` | องค์กรที่คุณเป็นสมาชิก |
| `POST /api/files` | อัปโหลด (multipart: `file`, ไม่บังคับ `folder`, `name`) จำกัด 100 MB; ตอบ 409 ถ้ามีชื่อนี้ใน folder นั้นแล้ว |
| `GET /api/files?folder=/` | ไฟล์และ folder ย่อยของ folder |
| `GET /api/files/{id}/download` | ไฟล์ที่ถอดรหัสแล้ว |
| `PATCH /api/files/{id}` | เปลี่ยนชื่อและ/หรือย้าย (`name`, `folder`) |
| `DELETE /api/files/{id}` | ย้ายไป Trash |
| `GET /api/trash`, `POST /api/trash/{id}/restore` | ดู Trash; กู้คืน (ตอบ 409 ถ้าชื่อถูกใช้ไปแล้ว) |
| `GET /api/audit?limit=&offset=` | audit log; เฉพาะ admin ขององค์กร |

## Role ของฐานข้อมูล

`postgres/init/10-create-roles.sh` สร้างสาม role ตอนเริ่มต้น data volume ครั้งแรก: `filestore_owner` (ใช้ migrate เท่านั้น), `filestore_app` (ของ API; ไม่มี `BYPASSRLS`) และ `filestore_backoffice` (เครื่องมือของ operator; มี `BYPASSRLS`) ถ้าจะเปลี่ยน role หรือรหัสผ่าน ต้องสร้าง volume ใหม่ (`docker compose down -v`)

## คำสั่งของ operator

องค์กร ผู้ใช้ และ Membership สร้างโดย Platform operator ไม่ได้สร้างผ่าน API:

```sh
docker compose run --rm ops create_tenant "Acme"
docker compose run --rm ops create_user alice@example.com 'a-password'
docker compose run --rm ops create_user --operator you@example.com 'a-password'   # a Platform operator: may sign in to the back office
docker compose run --rm ops add_membership alice@example.com "Acme" admin   # admin | member
docker compose run --rm ops destroy_tenant_key "Acme"    # its files become unreadable for good
docker compose run --rm ops check_file_objects           # every file record has its stored object
docker compose run --rm ops purge                         # see "Purge" below
```

- `create_user --operator` สร้าง Platform operator ที่ล็อกอินหลังบ้านได้
- `add_membership` ใส่ผู้ใช้เข้าองค์กรพร้อม role (`admin` หรือ `member`)
- `destroy_tenant_key` ทำให้ไฟล์ทั้งหมดขององค์กรนั้นอ่านไม่ได้ถาวร
- `check_file_objects` ตรวจว่าทุกระเบียนไฟล์มี object ที่เก็บไว้จริง
- `purge` ดูหัวข้อ "Purge" ด้านล่าง

## หลังบ้าน (Back office)

Platform operator ใช้ Django admin ที่ http://127.0.0.1:8001/admin/ (เข้าได้จากเครื่องตัวเองเท่านั้น) มันเป็น service แยก: เชื่อม Postgres ด้วย role `filestore_backoffice` มี secret key ของตัวเอง (`BACKOFFICE_SECRET_KEY`) และ **ไม่ได้รับทั้ง master key และ S3 credential** จึงไม่มีอะไรในนั้นอ่านหรือถอดรหัสไฟล์ได้ ผู้ที่ล็อกอินได้มีเฉพาะผู้ใช้ที่สร้างด้วย `create_user --operator` (หรือที่ติ๊ก "is operator" ในหลังบ้าน)

สิ่งที่ operator ทำได้: ดูไฟล์ของทุกองค์กร (ชื่อ, folder, ขนาด, สถานะ), ย้ายไฟล์ใดๆ ไป Trash และกู้คืน (ปฏิเสธการกู้ถ้าชื่อถูกใช้ไปแล้ว), สร้างและแก้ไของค์กร ผู้ใช้ Membership และ role, และอ่าน audit log ของทุกองค์กร ลบไฟล์ องค์กร หรือผู้ใช้จากที่นี่ไม่ได้ รายการไฟล์เป็นแบบอ่านอย่างเดียว

operator รีเซ็ตรหัสผ่านของผู้ใช้คนใดก็ได้ หรือเพิ่มตัวเองเข้าองค์กร แล้วล็อกอินแอปเป็นผู้ใช้คนนั้นเพื่ออ่านไฟล์ได้ ข้อนี้ป้องกันไม่ได้ตราบใดที่ operator ดูแลผู้ใช้ จึงทำให้มองเห็นแทน: ทุกการกระทำกับไฟล์ การเปลี่ยน membership การรีเซ็ตรหัสผ่าน และการทำลาย key โดย operator จะปรากฏใน audit log ขององค์กรนั้นเองในชื่อ "Platform operator" (ไม่ระบุชื่อบุคคล แต่ admin log ของหลังบ้านเองบันทึกชื่อไว้)

## Purge

`ops purge` ลบ upload ที่ค้างไม่เสร็จนานเกิน 1 ชั่วโมง (ทั้งระเบียนและ object ที่เก็บ) และไฟล์ใน Trash เกิน 7 วัน (ทั้งระเบียนและ object ที่เก็บ) รันซ้ำได้อย่างปลอดภัย มันจะลบ object ของไฟล์ใน Trash ก็ต่อเมื่อ Backup มีสำเนาครบแล้วเท่านั้น (key เดียวกัน ขนาดเต็ม) ดังนั้นเมื่อปิด Backup หรือ Backup ยังตามไม่ทัน ไฟล์ใน Trash ที่หมดอายุจะค้างอยู่จนกว่าจะรันรอบหลัง ตัวเลือก: `--tenant NAME`, `--pending-hours`, `--trash-days`

service `maintenance` รันมันทุก `PURGE_INTERVAL` วินาที (ค่าเริ่มต้น 3600; รอบแรกเกิดหลังเริ่มไปหนึ่งช่วงเวลา) เมื่อปิด profile `backup` มันทำความสะอาดเฉพาะ upload ที่ค้างไม่เสร็จ

## Backup และ restore

ปิดไว้เป็นค่าเริ่มต้น (ไม่งั้นมันจะคัดลอกทุกไฟล์ที่เทสต์อัปโหลด) เปิดด้วย `COMPOSE_PROFILES=backup`:

```sh
COMPOSE_PROFILES=backup docker compose up -d --build --wait   # starts filer-backup and db-backup
COMPOSE_PROFILES=backup docker compose run --rm db-backup once   # take a Backup now
```

Backup อยู่ใน `./backups`: `files/` (ไฟล์ที่เก็บไว้ ซึ่งยังเข้ารหัสอยู่ คัดลอกต่อเนื่อง ไม่สะท้อนการลบ) และ `db/` (dump หนึ่งไฟล์ต่อหนึ่ง Backup ใช้ได้ก็ต่อเมื่อมีไฟล์ marker `.complete` ซึ่งเขียนหลังจากทุกไฟล์ที่ dump อ้างถึงมาอยู่ใน `files/` แล้ว) Backup ไม่เคยมี master key อยู่ข้างใน

`./scripts/restore-drill.sh` พิสูจน์เรื่องนี้: ทำ Backup, ทำลาย stack พร้อม volume ทั้งหมด, สร้างใหม่จาก `./backups` บวกไฟล์ master key แล้วตรวจว่าไฟล์ list, ดาวน์โหลด และถอดรหัสได้, ไม่มีระเบียนใดชี้ไปไฟล์ที่หายไป, ฐานข้อมูลที่กู้มายังคงสิทธิ์เดิม, และ master key ที่ผิดอ่านอะไรไม่ได้เลย **สคริปต์นี้ลบ volume ของโปรเจกต์นี้และ `./backups` ทิ้ง**

## ทดสอบ

```sh
./scripts/test.sh          # black-box API tests inside the compose network + host port checks (needs uv)
./scripts/smoke.sh         # the four claims of the PoC in one command (see below); ends with the destructive restore drill
./scripts/restore-drill.sh # backup and restore (destructive, see above)
./scripts/e2e.sh           # the React app in a real browser (needs node and jq; downloads Chromium once)
```

- `test.sh` ทดสอบ API แบบ black-box ภายในเครือข่ายของ compose บวกตรวจพอร์ตจากเครื่อง (ต้องมี `uv`)
- `smoke.sh` พิสูจน์สี่ข้อของ PoC ในคำสั่งเดียว (ดูด้านล่าง) และจบด้วย restore drill ที่ลบข้อมูล
- `restore-drill.sh` ทดสอบ backup และ restore (ลบข้อมูล ดูด้านบน)
- `e2e.sh` ทดสอบแอป React ใน browser จริง (ต้องมี node และ jq; ดาวน์โหลด Chromium ครั้งแรก)

### Smoke test

`./scripts/smoke.sh` พิสูจน์บน stack จริงว่า (1) องค์กรที่ขอ file id ขององค์กรอื่นได้ 404, (2) query แบบไม่กรองด้วย role ของ API บนฐานข้อมูลไม่เห็นอะไรขององค์กรอื่นเลย (และไม่เห็นอะไรเลยเมื่อยังไม่ได้เลือกองค์กร), (3) สิ่งที่ SeaweedFS เก็บเป็น ciphertext โดยอ่านทั้งตัว object และไฟล์ volume ดิบ, และ (4) สร้าง stack ใหม่จาก Backup บวก master key ที่เก็บแยกได้ (restore drill) มันออกด้วย exit code ไม่ใช่ศูนย์เมื่อมีอะไรล้มเหลว

มันยังจงใจทำให้ RLS, การเข้ารหัส และสิทธิ์ข้ามองค์กรพัง แล้วบังคับให้การตรวจของมันเองต้องจับได้ เพื่อให้ผลผ่านไม่ใช่เรื่องบังเอิญ และทุกข้อ "ไม่พบอะไร" มี control เชิงบวกกำกับ ตั้ง `SMOKE_SKIP_DRILL=1` เพื่อข้ามขั้นสุดท้ายที่ลบข้อมูลสำหรับรันเร็ว (ประมาณ 40 วินาที)

สองสิ่งที่ได้เรียนรู้เกี่ยวกับ SeaweedFS ระหว่างสร้าง: มันบีบอัดข้อมูลที่บีบอัดได้บน volume และ `grep` ของ busybox ในโหมด regex ค้นข้อมูลไบนารีไม่ได้ การค้นหา plaintext ในไฟล์ volume จึงต้องใช้ข้อมูลทดสอบที่บีบอัดไม่ได้ และ `grep -F`

## ข้อจำกัดที่ทราบ (proof of concept ที่รันในเครื่อง)

- ไม่มี TLS; ทุกอย่างอยู่บน loopback token เก็บใน `localStorage` ของ browser; เพิกถอน refresh token ไม่ได้
- ไม่มีการจำกัดความถี่การล็อกอิน ทั้งที่ API และหลังบ้าน
- `GET /api/health` ไม่ต้องยืนยันตัวตน และทำการเขียน อ่าน ลบ S3 ทุกครั้งที่เรียก (ใช้เป็น healthcheck ของ container ด้วย)
- identity `admin` ของ S3 บน SeaweedFS มีอยู่ในทุกการติดตั้ง แม้มีแต่เทสต์ที่ใช้
- upload ที่ใหญ่กว่า 100 MB ถูกพักลงดิสก์ก่อนที่ API จะปฏิเสธ (เฉพาะสมาชิกองค์กรที่ล็อกอินแล้วเท่านั้นที่ไปถึงขั้นนั้น); แอป React ปฏิเสธก่อนส่ง
- การดาวน์โหลดที่ segment หลังๆ ไม่ผ่านการตรวจความสมบูรณ์จะถูกตัดกลางทาง (segment แรกถูกตรวจก่อนเริ่มตอบ) และยังถูกบันทึกใน audit log
- `./backups` มี dump ฐานข้อมูลแบบ plaintext (hash รหัสผ่านทุกตัวและ Tenant key ที่ห่อแล้วทุกตัว; ไม่มี master key ก็ใช้ไม่ได้) สร้างแบบเจ้าของคนเดียวอ่านได้ ควรคงไว้อย่างนั้น
