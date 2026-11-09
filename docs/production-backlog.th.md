# Backlog สำหรับ production

สิ่งที่ยังขาดระหว่าง PoC นี้กับการใช้งานจริง ยังไม่ได้ทำสิ่งใดในรายการนี้ ป้ายกำกับของแต่ละข้อ:
**[repo]** อ่านจาก repo นี้, **[docs]** มาจากเอกสารหรือ source ของ SeaweedFS ยังไม่ได้รันบน 4.52,
**[open]** ยังไม่รู้ ต้องตรวจ ตัวติดตามงานคือ Beads ให้แปลงเป็น bead เมื่อจะเริ่มทำข้อนั้น
ฉบับภาษาอังกฤษ: `production-backlog.md`

## 1. Topology ของ storage

ตอนนี้: `weed server` ตัวเดียว (master + volume + filer + S3) ใน container เดียว และ filer metadata อยู่ใน
leveldb บน volume `seaweed-data` **[repo]** process เดียวคือ failure domain เดียว และปรับขนาดแยกส่วนไม่ได้

| ส่วน | PoC | Production |
|---|---|---|
| master | 1 ตัว ใน `weed server` | 3 ตัว (raft ต้องเป็นเลขคี่) บนต่าง host |
| volume | 1 | 2 ตัวขึ้นไป คนละ host หรือ rack |
| filer | 1 ตัว + leveldb | 2 ตัวขึ้นไป + metadata store ที่ใช้ร่วมกัน |
| S3 | ใน `weed server` | `weed s3` แยก ไม่เก็บ state หลายตัวหลัง load balancer |
| admin | 1 | 1 ตัว สำหรับผู้ดูแลระบบเท่านั้น |

- Compose บนเครื่องเดียวไม่ได้ HA ต่อให้แยก container ต้องใช้หลาย host (Kubernetes, Nomad หรือเทียบเท่า) **[docs]**
- **Filer metadata store** filer หลายตัวใช้ leveldb ร่วมกันไม่ได้ ต้องมี store ที่ใช้ร่วมกัน (รองรับ Postgres)
  ใช้ database และ role แยกจาก DB ของ app ไม่ให้ชนกับ RLS และต้องสำรองไว้ด้วย เพราะถ้า metadata หายแต่ volume ยังอยู่
  ไฟล์จะหาไม่เจอ ข้อนี้กลับคำตัดสินเดิมที่ว่า "ข้าม" ซึ่งเหมาะกับ PoC ที่มี filer ตัวเดียว **[docs]**
- **Replication** `001` (rack เดียวกัน) ที่ถอนไปเพราะทุกสำเนาอยู่ดิสก์เดียว จะมีความหมายเมื่อ volume server อยู่คนละ
  host หรือ rack: ใช้ `010` (ต่าง rack) หรือ `100` (ต่าง data center) **[docs]**
- **Replication ไม่ใช่ backup** การลบหรือไฟล์เสียหายจะลอกไปทุกสำเนา `filer.backup` ยังต้องมี และ sink ต้องย้ายจาก
  local directory ไปที่เก็บนอกเครื่องหรือนอกพื้นที่ **[docs]**
- ต้องรัน `scripts/restore-drill.sh` ใหม่กับ topology ใหม่ ว่า `filer.backup` และ `filer.copy` ทำงานเหมือนเดิมกับ filer
  หรือ master หลายตัวหรือไม่ **[open]**

## 2. ค่าของ PoC ที่ต้องเปลี่ยน

- `-master.volumeSizeLimitMB=256 -volume.max=100` ใน `compose.yaml` จำกัด volume server ที่ราว 25 GB ต้องปรับตาม
  ข้อมูลจริง **[repo]**
- Identity `admin` ของ S3 ถูก render ในทุก deployment (ระบุว่าเป็น shortcut ใน `compose.yaml`) ต้องเอาออกใน
  production **[repo]**
- Filer และ master รับ request โดยไม่ต้อง authenticate ต้องอยู่ใน network ภายในเท่านั้น (ตอนนี้เป็นเช่นนั้นแล้ว
  **[repo]**) และพิจารณา `security.toml` (TLS/JWT ระหว่าง component) **[docs]**

## 3. นอกเหนือจาก storage

- **TLS** README ระบุว่า "No TLS; everything is on loopback" ถ้าไม่มี token จะวิ่งเป็นข้อความธรรมดา ทางเลือกหนึ่งคือ
  `compose.tls.yaml` แบบ opt-in (Traefik + Let's Encrypt แบบเดียวกับ `seaweedfs-stack`) เปิดเฉพาะ backend กับ
  frontend ห้ามเปิด `seaweedfs-admin`, filer หรือ S3 **[repo]**
- **Master key** ย้ายจากไฟล์ไป KMS หรือ HSM ADR 0001 ระบุว่า PoC ไม่มี KMS จริง **[repo]**
- **Postgres** ต้องมี HA และ point-in-time recovery ซึ่ง `pg_dump` รายชั่วโมงให้ไม่ได้ **[repo]**
- **Connection pooler** ใน repo ไม่มี pooler จึงเป็นความเสี่ยงในอนาคต ไม่ใช่ความเสี่ยงที่มีอยู่ **[repo]** ค่า tenant
  ถูกตั้งด้วย `set_config(..., true)` ภายใน transaction ของ request (`ATOMIC_REQUESTS`, `backend/accounts/rls.py`)
  ซึ่งเป็นรูปแบบ transaction-local ที่ปลอดภัยหลัง pooler แบบ transaction-mode ยังไม่เคยลองกับ pooler จริง **[open]**
  ถ้าจะเพิ่ม: ทุกค่าต้องเป็น transaction-local (ห้ามใช้ `SET` ระดับ session) และปิด server-side cursors ของ Django
  ตามที่เอกสารของ Django กำหนดสำหรับ transaction pooling
- **Runtime ของ backend และ migration** ต้องยืนยันว่าใช้ server ระดับ production และมีวิธีรัน migration ที่ปลอดภัยเมื่อมี
  หลาย instance ยังไม่ได้ตรวจ **[open]**

## 4. ลำดับที่แนะนำ

1. เลือก topology: กี่ host และ data center เดียวหรือหลายแห่ง
2. แยก master / volume / filer / S3
3. ย้าย filer store ไป Postgres ตัวแยก
4. ตั้ง replication ให้ตรงกับ topology
5. ย้าย sink ของ `filer.backup` ออกนอกเครื่อง แล้วรัน restore drill ใหม่

## 5. เปรียบเทียบกับ `seaweedfs-stack` (2026-11-07)

เปรียบเทียบ `/Users/colonxzerbirst/Project/seaweedfs-stack` กับ repo นี้ โดยอ่านไฟล์อย่างเดียว ไม่ได้รัน stack นั้น

**ที่ apply แล้ว**

- `restart: unless-stopped` ให้ `postgres`, `seaweedfs`, `seaweedfs-admin`, `backend`, `backoffice`, `frontend`
  (`maintenance`, `filer-backup` และ `db-backup` มีอยู่เดิม) **[repo]**
- `SEAWEEDFS_VERSION` (ค่าเริ่มต้น `4.52`) แทน tag ที่เขียนซ้ำ 5 จุดใน `compose.yaml` และมีบรรทัดตัวเลือกใน
  `.env.example` `docker compose config` resolve ได้ทุก profile **[repo]**
- `scripts/test.sh` ผ่านหลังแก้: 156 compose tests และ 15 host checks ยังไม่ได้รัน `e2e.sh`, `restore-drill.sh`
  และ `smoke.sh` ซ้ำ เพราะเป็นการแก้ compose ล้วน

**เอามาได้ แต่เลื่อนไปตอน deploy**

- Firewall ของ host: เปิดเฉพาะ 22, 80 และ 443 (`ufw`) มีความหมายเมื่อมี TLS proxy สาธารณะอยู่ด้านหน้า (หัวข้อ 3)
  ตอนนี้ทุกพอร์ตที่เปิดสู่ host bind `127.0.0.1` ไม่ใช่การแก้ repo แต่เป็นขั้นตอน deploy **[docs]**
- TLS ผ่าน Traefik และ `socket-proxy` (Traefik อ่าน `docker.sock` แบบ read-only): ดูหัวข้อ 3

**ที่ไม่เอามา และเหตุผล**

| สิ่งที่เขาทำ | ทำไมไม่ใช้ที่นี่ |
|---|---|
| Admin UI บนโดเมนสาธารณะหลัง Traefik | ของเรา bind `127.0.0.1:12535` UI นี้ไม่รู้จัก Tenant และข้าม Postgres กับ RLS |
| รหัส admin เป็น flag `-adminPassword=...` | เห็นได้ใน process list ของเราใช้ env `WEED_ADMIN_PASSWORD` |
| `s3-config.json` แบบ static แก้ placeholder ด้วยมือ | ของเรา render `s3.json` จาก `.env` ตอน start ไม่มี secret ในไฟล์ที่ track |
| `SEAWEEDFS_VERSION=latest` | ของเรา pin เวอร์ชันที่ทดสอบแล้ว |
| Bind mount `./volume1-data`, `./volume2-data` | named volume + `filer.backup` ครอบคลุม backup และ restore (ผ่าน drill) |
| Replication `001` บน volume server 2 ตัว | host เดียว rack เดียว ทุกสำเนาอยู่ดิสก์เดียวกัน ทบทวนเมื่อมี topology จริง (หัวข้อ 1) |
| Filer metadata ใน Postgres | ไม่จำเป็นเมื่อมี filer ตัวเดียว จะจำเป็นเมื่อมีหลายตัว (หัวข้อ 1) |
| `chmod 600 .env` | `.env` คง mode 660 ตามค่าที่ตั้งไว้ |
