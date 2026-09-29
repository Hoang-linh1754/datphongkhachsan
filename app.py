import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import streamlit as st

DB_PATH = "hotel.db"
HOTEL_NAME = "Khách sạn Demo"
STAFF_USER = os.getenv("HOTEL_STAFF_USER", "admin")
STAFF_PASSWORD = os.getenv("HOTEL_STAFF_PASSWORD", "123456")

ROOM_TYPES = {
    "Standard": {"price": 500_000, "capacity": 2, "description": "Phòng tiêu chuẩn, 1 giường đôi"},
    "Deluxe": {"price": 800_000, "capacity": 2, "description": "Phòng cao cấp, có cửa sổ"},
    "Family": {"price": 1_200_000, "capacity": 4, "description": "Phòng gia đình, rộng rãi"},
    "Suite": {"price": 1_800_000, "capacity": 2, "description": "Phòng Suite cao cấp"},
}

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_number TEXT UNIQUE NOT NULL,
            room_type TEXT NOT NULL,
            price INTEGER NOT NULL,
            capacity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Trống'
        );
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_code TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            room_id INTEGER NOT NULL,
            checkin TEXT NOT NULL,
            checkout TEXT NOT NULL,
            guests INTEGER NOT NULL,
            nights INTEGER NOT NULL,
            total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Đã xác nhận',
            payment_status TEXT NOT NULL DEFAULT 'Chưa thanh toán',
            created_at TEXT NOT NULL,
            FOREIGN KEY(room_id) REFERENCES rooms(id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_code TEXT NOT NULL,
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        count = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        if count == 0:
            rooms = [
                ("101", "Standard"), ("102", "Standard"), ("103", "Standard"),
                ("201", "Deluxe"), ("202", "Deluxe"), ("203", "Deluxe"),
                ("301", "Family"), ("302", "Family"),
                ("401", "Suite"), ("402", "Suite"),
                ("501", "Standard"), ("502", "Deluxe"),
            ]
            for number, kind in rooms:
                info = ROOM_TYPES[kind]
                conn.execute(
                    "INSERT INTO rooms(room_number, room_type, price, capacity, status) VALUES(?,?,?,?,?)",
                    (number, kind, info["price"], info["capacity"], "Trống")
                )

            # 4 booking mẫu: 2 đang lưu trú, 2 đặt trước; 3 đã thanh toán.
            today = date.today()
            samples = [
                ("KHACH001", "Nguyễn Văn An", "0901234567", "an@example.com", "101", today, today + timedelta(days=2), 2, "Đang ở", "Đã thanh toán"),
                ("KHACH002", "Trần Thị Bình", "0912345678", "binh@example.com", "201", today, today + timedelta(days=3), 2, "Đang ở", "Đã thanh toán"),
                ("KHACH003", "Lê Minh Cường", "0987654321", "cuong@example.com", "301", today + timedelta(days=2), today + timedelta(days=4), 4, "Đã xác nhận", "Đã thanh toán"),
                ("KHACH004", "Phạm Thu Hà", "0934567890", "ha@example.com", "401", today + timedelta(days=3), today + timedelta(days=5), 2, "Đã xác nhận", "Chưa thanh toán"),
            ]
            for code, name, phone, email, room_no, ci, co, guests, status, payment in samples:
                room = conn.execute("SELECT * FROM rooms WHERE room_number=?", (room_no,)).fetchone()
                nights = (co - ci).days
                total = room["price"] * nights
                conn.execute("""
                    INSERT INTO bookings(booking_code, customer_name, phone, email, room_id,
                    checkin, checkout, guests, nights, total, status, payment_status, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (code, name, phone, email, room["id"], ci.isoformat(), co.isoformat(),
                      guests, nights, total, status, payment, datetime.now().isoformat(timespec="seconds")))
                room_status = "Đang có khách" if status == "Đang ở" else "Đã đặt"
                conn.execute("UPDATE rooms SET status=? WHERE id=?", (room_status, room["id"]))

def money(value):
    return f"{int(value):,}".replace(",", ".") + " đ"

def available_rooms(checkin, checkout, room_type=None, guests=1):
    with get_conn() as conn:
        sql = """
        SELECT r.* FROM rooms r
        WHERE r.capacity >= ?
        AND r.id NOT IN (
            SELECT b.room_id FROM bookings b
            WHERE b.status IN ('Đã xác nhận','Đang ở')
              AND date(b.checkin) < date(?)
              AND date(b.checkout) > date(?)
        )
        """
        params = [guests, checkout.isoformat(), checkin.isoformat()]
        if room_type and room_type != "Tất cả":
            sql += " AND r.room_type=?"
            params.append(room_type)
        sql += " ORDER BY r.room_number"
        return conn.execute(sql, params).fetchall()

def create_booking(name, phone, email, room_id, checkin, checkout, guests):
    with get_conn() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        if not room:
            raise ValueError("Không tìm thấy phòng.")
        conflict = conn.execute("""
            SELECT COUNT(*) FROM bookings
            WHERE room_id=? AND status IN ('Đã xác nhận','Đang ở')
              AND date(checkin) < date(?) AND date(checkout) > date(?)
        """, (room_id, checkout.isoformat(), checkin.isoformat())).fetchone()[0]
        if conflict:
            raise ValueError("Phòng vừa được đặt trong khoảng thời gian này. Vui lòng chọn phòng khác.")
        nights = (checkout - checkin).days
        code = "BK" + uuid.uuid4().hex[:8].upper()
        total = room["price"] * nights
        conn.execute("""
            INSERT INTO bookings(booking_code, customer_name, phone, email, room_id, checkin,
            checkout, guests, nights, total, status, payment_status, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (code, name.strip(), phone.strip(), email.strip(), room_id, checkin.isoformat(),
              checkout.isoformat(), guests, nights, total, "Đã xác nhận", "Chưa thanh toán",
              datetime.now().isoformat(timespec="seconds")))
        conn.execute("UPDATE rooms SET status='Đã đặt' WHERE id=?", (room_id,))
        return code, total

def fetch_bookings():
    with get_conn() as conn:
        return conn.execute("""
            SELECT b.*, r.room_number, r.room_type FROM bookings b
            JOIN rooms r ON r.id=b.room_id ORDER BY b.id DESC
        """).fetchall()

def refresh_room_statuses():
    # Trạng thái tổng quan dựa trên các lượt lưu trú hiện tại và đặt trước.
    today = date.today().isoformat()
    with get_conn() as conn:
        rooms = conn.execute("SELECT id FROM rooms").fetchall()
        for room in rooms:
            active = conn.execute("""
                SELECT status, checkin, checkout FROM bookings
                WHERE room_id=? AND status IN ('Đã xác nhận','Đang ở')
                AND date(checkin) <= date(?) AND date(checkout) > date(?)
                ORDER BY CASE status WHEN 'Đang ở' THEN 0 ELSE 1 END LIMIT 1
            """, (room["id"], today, today)).fetchone()
            if active:
                status = "Đang có khách" if active["status"] == "Đang ở" else "Đã đặt"
            else:
                status = "Trống"
            conn.execute("UPDATE rooms SET status=? WHERE id=?", (status, room["id"]))

def ai_reply(question):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": f"""Bạn là trợ lý tư vấn của {HOTEL_NAME}.
Trả lời bằng tiếng Việt, lịch sự, ngắn gọn. Bảng giá tham khảo: """ +
                     "; ".join(f"{k}: {money(v['price'])}/đêm, tối đa {v['capacity']} khách" for k, v in ROOM_TYPES.items()) +
                     ". Không tự khẳng định phòng còn trống; hướng dẫn khách kiểm tra mục Đặt phòng."},
                    {"role": "user", "content": question}
                ],
                temperature=0.5,
            )
            return response.choices[0].message.content or "Xin lỗi, tôi chưa có câu trả lời."
        except Exception as exc:
            return f"Không kết nối được AI ( {exc} ). Bạn có thể hỏi về giá phòng hoặc cách đặt phòng."
    q = question.lower()
    if any(x in q for x in ["giá", "bao nhiêu", "phòng"]):
        return "Bảng giá theo đêm:\n" + "\n".join(
            f"• {k}: {money(v['price'])} — tối đa {v['capacity']} khách. {v['description']}"
            for k, v in ROOM_TYPES.items()
        ) + "\n\nBạn có thể kiểm tra ngày còn phòng tại mục **Đặt phòng**."
    if any(x in q for x in ["đặt", "booking", "đặt phòng"]):
        return "Bạn vào mục **Đặt phòng**, nhập thông tin và chọn ngày nhận/trả phòng. Hệ thống sẽ hiển thị phòng còn trống và tổng tiền."
    if any(x in q for x in ["check-in", "nhận phòng", "trả phòng", "check-out"]):
        return "Bạn vui lòng cung cấp mã đặt phòng trong khung **Chat với nhân viên** để được hỗ trợ về thời gian nhận/trả phòng."
    return "Xin chào! Tôi có thể tư vấn giá phòng, loại phòng và hướng dẫn đặt phòng. Bạn muốn tìm hiểu nội dung nào?"

def main():
    st.set_page_config(page_title="Quản lý khách sạn", page_icon="🏨", layout="wide")
    init_db()
    refresh_room_statuses()
    st.title("🏨 Hệ thống đặt phòng khách sạn")
    st.caption("Demo Streamlit • Dữ liệu lưu trong SQLite (hotel.db)")

    with st.sidebar:
        st.header("Điều hướng")
        page = st.radio("Chọn chức năng", [
            "Tổng quan", "Đặt phòng", "Sơ đồ phòng",
            "Thông tin đặt phòng", "Chat với AI", "Chat với nhân viên", "Quản trị nhân viên"
        ])
        st.divider()
        st.markdown("**Tài khoản nhân viên demo**")
        st.code(f"Tài khoản: {STAFF_USER}\nMật khẩu: {STAFF_PASSWORD}")
        st.caption("Đổi thông tin bằng biến môi trường trước khi triển khai thật.")

    bookings = fetch_bookings()

    if page == "Tổng quan":
        st.subheader("📊 Tổng quan khách sạn")
        with get_conn() as conn:
            room_count = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
            occupied = conn.execute("SELECT COUNT(*) FROM rooms WHERE status='Đang có khách'").fetchone()[0]
            reserved = conn.execute("SELECT COUNT(*) FROM rooms WHERE status='Đã đặt'").fetchone()[0]
            revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM bookings WHERE payment_status='Đã thanh toán'").fetchone()[0]
            booking_count = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tổng số phòng", room_count)
        c2.metric("Đang có khách", occupied)
        c3.metric("Đã đặt trước", reserved)
        c4.metric("Doanh thu đã thanh toán", money(revenue))
        c5, c6 = st.columns(2)
        c5.metric("Tổng lượt đặt phòng", booking_count)
        c6.metric("Tỷ lệ phòng đang có khách", f"{occupied / room_count * 100:.1f}%" if room_count else "0%")
        st.subheader("📌 Các đặt phòng gần đây")
        if bookings:
            st.dataframe([dict(b) for b in bookings[:10]], use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có đặt phòng.")

    elif page == "Đặt phòng":
        st.subheader("🛎️ Đặt phòng khách sạn")
        with st.form("booking_form", clear_on_submit=False):
            col1, col2 = st.columns(2)
            name = col1.text_input("Họ và tên *")
            phone = col2.text_input("Số điện thoại *")
            email = col1.text_input("Email")
            guests = col2.number_input("Số khách", min_value=1, max_value=10, value=2)
            today = date.today()
            col3, col4 = st.columns(2)
            checkin = col3.date_input("Ngày nhận phòng", value=today + timedelta(days=1), min_value=today)
            checkout = col4.date_input("Ngày trả phòng", value=today + timedelta(days=2), min_value=today + timedelta(days=1))
            room_type = st.selectbox("Loại phòng", ["Tất cả"] + list(ROOM_TYPES.keys()))
            submitted_search = st.form_submit_button("🔎 Tìm phòng trống")
        if submitted_search:
            if checkout <= checkin:
                st.error("Ngày trả phòng phải sau ngày nhận phòng.")
            else:
                options = available_rooms(checkin, checkout, room_type, int(guests))
                st.session_state["available_room_ids"] = [r["id"] for r in options]
                st.session_state["booking_search"] = {
                    "checkin": checkin.isoformat(), "checkout": checkout.isoformat(),
                    "guests": int(guests), "name": name, "phone": phone, "email": email
                }
                if options:
                    st.success(f"Tìm thấy {len(options)} phòng phù hợp.")
                else:
                    st.warning("Không có phòng trống phù hợp trong khoảng ngày đã chọn.")
        room_ids = st.session_state.get("available_room_ids", [])
        search = st.session_state.get("booking_search")
        if room_ids and search:
            with get_conn() as conn:
                placeholders = ",".join("?" for _ in room_ids)
                options = conn.execute(f"SELECT * FROM rooms WHERE id IN ({placeholders}) ORDER BY room_number", room_ids).fetchall()
            labels = {r["id"]: f"Phòng {r['room_number']} • {r['room_type']} • {money(r['price'])}/đêm • tối đa {r['capacity']} khách" for r in options}
            selected = st.selectbox("Chọn phòng", list(labels), format_func=lambda x: labels[x])
            nights = (date.fromisoformat(search["checkout"]) - date.fromisoformat(search["checkin"])).days
            selected_room = next(r for r in options if r["id"] == selected)
            st.info(f"Thời gian: {nights} đêm • Tổng tiền: **{money(selected_room['price'] * nights)}**")
            if st.button("✅ Xác nhận đặt phòng", type="primary"):
                if not search["name"].strip() or not search["phone"].strip():
                    st.error("Vui lòng nhập họ tên và số điện thoại trước khi tìm phòng.")
                else:
                    try:
                        code, total = create_booking(
                            search["name"], search["phone"], search["email"], selected,
                            date.fromisoformat(search["checkin"]), date.fromisoformat(search["checkout"]),
                            search["guests"]
                        )
                        st.success(f"Đặt phòng thành công! Mã đặt phòng: **{code}**. Tổng tiền: **{money(total)}**. Vui lòng lưu mã để tra cứu.")
                        st.session_state.pop("available_room_ids", None)
                    except ValueError as e:
                        st.error(str(e))

    elif page == "Sơ đồ phòng":
        st.subheader("🗺️ Sơ đồ phòng")
        st.caption("🟢 Trống  •  🔴 Đang có khách  •  🟠 Đã đặt  •  ⚫ Bảo trì")
        with get_conn() as conn:
            rooms = conn.execute("SELECT * FROM rooms ORDER BY room_number").fetchall()
        cols = st.columns(4)
        for i, room in enumerate(rooms):
            color = {"Trống": "#d8f3dc", "Đang có khách": "#ffd6d6", "Đã đặt": "#ffe8b3", "Bảo trì": "#dddddd"}.get(room["status"], "#eeeeee")
            with cols[i % 4]:
                st.markdown(
                    f"""<div style="background:{color};padding:18px;border-radius:12px;margin-bottom:12px;color:#222">
                    <h3 style="margin:0">Phòng {room['room_number']}</h3>
                    <b>{room['room_type']}</b><br>{money(room['price'])}/đêm<br>
                    <b>{room['status']}</b><br>Sức chứa: {room['capacity']} khách
                    </div>""", unsafe_allow_html=True
                )

    elif page == "Thông tin đặt phòng":
        st.subheader("🔎 Tra cứu thông tin đặt phòng")
        code = st.text_input("Nhập mã đặt phòng")
        phone = st.text_input("Số điện thoại dùng khi đặt phòng")
        if st.button("Tra cứu"):
            with get_conn() as conn:
                result = conn.execute("""
                    SELECT b.*, r.room_number, r.room_type FROM bookings b
                    JOIN rooms r ON r.id=b.room_id
                    WHERE upper(b.booking_code)=upper(?) AND b.phone=?
                """, (code.strip(), phone.strip())).fetchone()
            if result:
                st.success("Tìm thấy thông tin đặt phòng.")
                a, b = st.columns(2)
                a.write(f"**Mã đặt phòng:** {result['booking_code']}")
                a.write(f"**Khách hàng:** {result['customer_name']}")
                a.write(f"**Phòng:** {result['room_number']} – {result['room_type']}")
                a.write(f"**Ngày nhận:** {result['checkin']}")
                b.write(f"**Ngày trả:** {result['checkout']}")
                b.write(f"**Số đêm:** {result['nights']}")
                b.write(f"**Tổng tiền:** {money(result['total'])}")
                b.write(f"**Trạng thái:** {result['status']}")
                b.write(f"**Thanh toán:** {result['payment_status']}")
            else:
                st.error("Không tìm thấy đặt phòng. Vui lòng kiểm tra lại mã và số điện thoại.")
        st.divider()
        st.markdown("**Các đặt phòng mẫu (dành cho trình diễn):**")
        st.dataframe([{"Mã": b["booking_code"], "Khách hàng": b["customer_name"], "Phòng": b["room_number"],
                       "Nhận phòng": b["checkin"], "Trả phòng": b["checkout"], "Tổng tiền": money(b["total"]),
                       "Trạng thái": b["status"], "Thanh toán": b["payment_status"]} for b in bookings],
                     use_container_width=True, hide_index=True)

    elif page == "Chat với AI":
        st.subheader("🤖 Trợ lý AI tư vấn khách sạn")
        st.caption("Nếu chưa cấu hình API key, chatbot sẽ trả lời bằng các câu trả lời mẫu.")
        if "ai_messages" not in st.session_state:
            st.session_state.ai_messages = []
        for msg in st.session_state.ai_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        prompt = st.chat_input("Nhập câu hỏi về giá phòng, dịch vụ hoặc cách đặt phòng...")
        if prompt:
            st.session_state.ai_messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            answer = ai_reply(prompt)
            st.session_state.ai_messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.markdown(answer)

    elif page == "Chat với nhân viên":
        st.subheader("💬 Trao đổi với nhân viên")
        code = st.text_input("Mã đặt phòng của bạn")
        if code:
            with get_conn() as conn:
                exists = conn.execute("SELECT booking_code FROM bookings WHERE upper(booking_code)=upper(?)", (code.strip(),)).fetchone()
                messages = conn.execute("SELECT * FROM messages WHERE upper(booking_code)=upper(?) ORDER BY id", (code.strip(),)).fetchall()
            if not exists:
                st.warning("Mã đặt phòng chưa đúng. Hãy kiểm tra lại trong mục Thông tin đặt phòng.")
            else:
                for msg in messages:
                    with st.chat_message("assistant" if msg["sender"] == "Nhân viên" else "user"):
                        st.caption(f"{msg['sender']} • {msg['created_at']}")
                        st.write(msg["message"])
                message = st.chat_input("Nhập tin nhắn gửi nhân viên...")
                if message:
                    with get_conn() as conn:
                        conn.execute("INSERT INTO messages(booking_code,sender,message,created_at) VALUES(?,?,?,?)",
                                     (code.strip().upper(), "Khách hàng", message, datetime.now().strftime("%Y-%m-%d %H:%M")))
                    st.rerun()

    elif page == "Quản trị nhân viên":
        st.subheader("🔐 Khu vực nhân viên")
        if not st.session_state.get("staff_logged_in"):
            with st.form("staff_login"):
                user = st.text_input("Tài khoản")
                password = st.text_input("Mật khẩu", type="password")
                login = st.form_submit_button("Đăng nhập")
            if login:
                if user == STAFF_USER and password == STAFF_PASSWORD:
                    st.session_state.staff_logged_in = True
                    st.rerun()
                else:
                    st.error("Sai tài khoản hoặc mật khẩu.")
        else:
            if st.button("Đăng xuất"):
                st.session_state.staff_logged_in = False
                st.rerun()
            st.markdown("### Quản lý đặt phòng")
            with get_conn() as conn:
                all_bookings = conn.execute("""
                    SELECT b.*, r.room_number, r.room_type FROM bookings b
                    JOIN rooms r ON r.id=b.room_id ORDER BY b.id DESC
                """).fetchall()
            if all_bookings:
                st.dataframe([{"Mã": b["booking_code"], "Khách": b["customer_name"], "Điện thoại": b["phone"],
                               "Phòng": b["room_number"], "Nhận": b["checkin"], "Trả": b["checkout"],
                               "Tổng tiền": money(b["total"]), "Trạng thái": b["status"],
                               "Thanh toán": b["payment_status"]} for b in all_bookings],
                             use_container_width=True, hide_index=True)
                chosen = st.selectbox("Chọn mã đặt phòng để cập nhật", [b["booking_code"] for b in all_bookings])
                current = next(b for b in all_bookings if b["booking_code"] == chosen)
                with st.form("update_booking"):
                    status = st.selectbox("Trạng thái đặt phòng", ["Đã xác nhận", "Đang ở", "Đã trả phòng", "Đã hủy"],
                                          index=["Đã xác nhận", "Đang ở", "Đã trả phòng", "Đã hủy"].index(current["status"]))
                    payment = st.selectbox("Thanh toán", ["Chưa thanh toán", "Đã thanh toán"],
                                           index=["Chưa thanh toán", "Đã thanh toán"].index(current["payment_status"]))
                    update = st.form_submit_button("Lưu cập nhật")
                if update:
                    with get_conn() as conn:
                        conn.execute("UPDATE bookings SET status=?, payment_status=? WHERE booking_code=?",
                                     (status, payment, chosen))
                    refresh_room_statuses()
                    st.success("Đã cập nhật đặt phòng.")
                    st.rerun()
            st.markdown("### Tin nhắn khách hàng")
            with get_conn() as conn:
                messages = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 100").fetchall()
            if not messages:
                st.info("Chưa có tin nhắn.")
            else:
                for msg in reversed(messages):
                    st.markdown(f"**{msg['booking_code']} | {msg['sender']} | {msg['created_at']}**")
                    st.write(msg["message"])
                    if msg["sender"] == "Khách hàng":
                        with st.form(f"reply_{msg['id']}"):
                            reply = st.text_input("Phản hồi", key=f"reply_text_{msg['id']}")
                            send = st.form_submit_button("Gửi phản hồi")
                        if send and reply.strip():
                            with get_conn() as conn:
                                conn.execute("INSERT INTO messages(booking_code,sender,message,created_at) VALUES(?,?,?,?)",
                                             (msg["booking_code"], "Nhân viên", reply.strip(),
                                              datetime.now().strftime("%Y-%m-%d %H:%M")))
                            st.success("Đã gửi phản hồi.")
                            st.rerun()

if __name__ == "__main__":
    main()
