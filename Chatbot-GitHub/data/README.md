# Schedule Chatbot Data Split

ไฟล์ข้อมูลจริงถูกแยกเป็น 4 ส่วน

- teacher_subjects.json: ข้อมูลอาจารย์ + รายวิชา + ยอดรวม
- schedule.json: ตารางสอนแต่ละคาบ
- classes.json: กลุ่มเรียน สท./ทค. + คาบเรียนรวมที่มีข้อมูลยืนยัน
- rules.json: กฎการตอบ, aliases, และกฎห้ามรวม student_count ข้ามคาบ

## สำคัญ
ยังไม่ควรลบ schedule_ground_truth_v4.json
ให้เก็บไว้เป็น backup/source เดิมจนกว่าระบบใหม่จะผ่านการทดสอบครบ
