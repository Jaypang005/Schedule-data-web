# Teacher Schedule Chatbot

เว็บแชตบอตสำหรับค้นข้อมูลตารางสอนของอาจารย์ พัฒนาด้วย Flask

## ไฟล์สำคัญ

- `app.py` — เว็บและ API
- `chatbot.py` — การประมวลผลคำถาม
- `data/` — ตารางสอน รายวิชา อาจารย์ และโมเดลภาษาภายใน
- `templates/` และ `static/` — หน้าเว็บ
- `render.yaml` — การตั้งค่าสำหรับ Render

## รันในเครื่อง

```bash
python -m pip install -r requirements.txt
python app.py
```

จากนั้นเปิด http://127.0.0.1:5000/

## นำขึ้น Render แบบ Blueprint

1. อัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ขึ้น GitHub โดยให้ `app.py` อยู่ที่หน้าแรกของ Repository
2. เข้า Render แล้วเลือก **New > Blueprint**
3. เชื่อม Repository นี้
4. Render จะอ่าน `render.yaml` และเตรียม Web Service ให้อัตโนมัติ
5. ตรวจสอบว่าเป็นแผน Free แล้วกด Deploy

## ตั้งค่า Render แบบ Web Service

หากไม่ใช้ Blueprint ให้ตั้งค่าดังนี้:

- Runtime: `Python 3`
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --bind 0.0.0.0:$PORT app:app`
- Environment: `SCHEDULE_AI_ENABLED=0`
- Environment: `SCHEDULE_PARSER_LOG=0`

ระบบบน Render ใช้ตัวแยกคำถามแบบกฎและข้อมูล JSON จึงไม่ต้องติดตั้ง PyTorch หรือดาวน์โหลดโมเดล Hugging Face
