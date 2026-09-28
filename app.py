
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# =========================================================
# 1. CẤU HÌNH
# =========================================================

st.set_page_config(
    page_title="Hotel Booking - Đặt phòng khách sạn",
    page_icon="🏨",
    layout="wide",
)

DB_NAME = "hotel_booking.db"

BOOKING_STATUSES = [
    "Chờ xác nhận",
    "Đã xác nhận",
    "Đã từ chối",
    "Đã hủy",
    "Đã hoàn tất",
]

ACTIVE_STATUSES = ["Chờ xác nhận", "Đã xác nhận"]

ROOM_TYPES = [
    "Phòng đơn",
    "Phòng đôi",
    "Phòng VIP",
    "Phòng gia đình",
    "Phòng Deluxe",
]

# Mật khẩu nhân viên dùng cho bản demo.
# Hãy thay đổi trước khi triển khai thực tế.
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD", "123456")


# =========================================================
# 2. DATABASE
# =========================================================

def connect_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_number TEXT UNIQUE NOT NULL,
            room_type TEXT NOT NULL,
            capacity INTEGER NOT NULL DEFAULT 2,
            price REAL NOT NULL DEFAULT 0,
            description TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_code TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT DEFAULT '',
            room_id INTEGER NOT NULL,
            checkin TEXT NOT NULL,
            checkout TEXT NOT NULL,
            guests INTEGER NOT NULL,
            nights INTEGER NOT NULL,
            total_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Chờ xác nhận',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_code TEXT NOT NULL,
            sender TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (booking_code)
                REFERENCES bookings(booking_code)
                ON DELETE CASCADE
        )
    """)

    # Dữ liệu mẫu
    room_count = conn.execute(
        "SELECT COUNT(*) FROM rooms"
    ).fetchone()[0]

    if room_count == 0:
        sample_rooms = [
            (
                "101", "Phòng đơn", 1, 350000,
                "Phòng đơn tiện nghi, phù hợp 1 khách."
            ),
            (
                "102", "Phòng đôi", 2, 500000,
                "Phòng đôi có giường lớn, phù hợp 2 khách."
            ),
            (
                "103", "Phòng VIP", 2, 900000,
                "Phòng VIP với không gian rộng rãi."
            ),
            (
                "201", "Phòng gia đình", 4, 1200000,
                "Phòng gia đình dành cho tối đa 4 khách."
            ),
            (
                "202", "Phòng Deluxe", 2, 750000,
                "Phòng Deluxe tiện nghi, view đẹp."
            ),
        ]

        conn.executemany("""
            INSERT INTO rooms (
                room_number, room_type, capacity, price, description
            ) VALUES (?, ?, ?, ?, ?)
        """, sample_rooms)

    conn.commit()
    conn.close()


def get_rooms():
    conn = connect_db()
    df = pd.read_sql_query(
        "SELECT * FROM rooms WHERE active = 1 ORDER BY room_number",
        conn,
    )
    conn.close()
    return df


def get_all_bookings():
    conn = connect_db()
    df = pd.read_sql_query("""
        SELECT
            b.*,
            r.room_number,
            r.room_type,
            r.capacity
        FROM bookings b
        JOIN rooms r ON b.room_id = r.id
        ORDER BY b.created_at DESC
    """, conn)
    conn.close()
    return df


def get_booking(code):
    conn = connect_db()
    booking = conn.execute("""
        SELECT
            b.*,
            r.room_number,
            r.room_type,
            r.capacity,
            r.price AS room_price
        FROM bookings b
        JOIN rooms r ON b.room_id = r.id
        WHERE b.booking_code = ?
    """, (code.strip().upper(),)).fetchone()
    conn.close()
    return booking


def get_messages(code):
    conn = connect_db()
    messages = conn.execute("""
        SELECT *
        FROM messages
        WHERE booking_code = ?
        ORDER BY id ASC
    """, (code.strip().upper(),)).fetchall()
    conn.close()
    return messages


# =========================================================
# 3. KIỂM TRA PHÒNG TRỐNG
# =========================================================

def available_rooms(checkin, checkout, guests):
    """
    Hai khoảng ngày bị xem là trùng nếu:
    ngày nhận mới < ngày trả cũ
    và ngày trả mới > ngày nhận cũ.
    """

    conn = connect_db()

    rooms = conn.execute("""
        SELECT *
        FROM rooms
        WHERE active = 1
          AND capacity >= ?
          AND id NOT IN (
              SELECT room_id
              FROM bookings
              WHERE status IN ('Chờ xác nhận', 'Đã xác nhận')
                AND checkin < ?
                AND checkout > ?
          )
        ORDER BY price ASC
    """, (
        guests,
        checkout.isoformat(),
        checkin.isoformat(),
    )).fetchall()

    conn.close()
    return rooms


# =========================================================
# 4. TẠO ĐẶT PHÒNG
# =========================================================

def create_booking(
    customer_name,
    phone,
    email,
    room_id,
    checkin,
    checkout,
    guests,
    note,
):
    if checkout <= checkin:
        return False, "Ngày trả phòng phải sau ngày nhận phòng."

    nights = (checkout - checkin).days

    conn = connect_db()

    try:
        # Kiểm tra và ghi đơn trong cùng một transaction.
        conn.execute("BEGIN IMMEDIATE")

        room = conn.execute("""
            SELECT *
            FROM rooms
            WHERE id = ? AND active = 1
        """, (room_id,)).fetchone()

        if room is None:
            conn.rollback()
            return False, "Phòng không tồn tại hoặc đã ngừng hoạt động."

        if guests > room["capacity"]:
            conn.rollback()
            return False, "Số khách vượt quá sức chứa của phòng."

        overlap = conn.execute("""
            SELECT COUNT(*)
            FROM bookings
            WHERE room_id = ?
              AND status IN ('Chờ xác nhận', 'Đã xác nhận')
              AND checkin < ?
              AND checkout > ?
        """, (
            room_id,
            checkout.isoformat(),
            checkin.isoformat(),
        )).fetchone()[0]

        if overlap > 0:
            conn.rollback()
            return False, "Phòng đã có người đặt trong khoảng ngày này."

        booking_code = (
            "BK" + datetime.now().strftime("%y%m%d")
            + uuid.uuid4().hex[:6].upper()
        )

        total_price = float(room["price"]) * nights

        conn.execute("""
            INSERT INTO bookings (
                booking_code, customer_name, phone, email,
                room_id, checkin, checkout, guests, nights,
                total_price, status, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            booking_code,
            customer_name.strip(),
            phone.strip(),
            email.strip(),
            room_id,
            checkin.isoformat(),
            checkout.isoformat(),
            guests,
            nights,
            total_price,
            "Chờ xác nhận",
            note.strip(),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

        conn.commit()
        return True, booking_code

    except Exception as e:
        conn.rollback()
        return False, f"Không thể tạo đặt phòng: {e}"

    finally:
        conn.close()


# =========================================================
# 5. CẬP NHẬT TRẠNG THÁI ĐẶT PHÒNG
# =========================================================

def update_booking_status(code, new_status):
    if new_status not in BOOKING_STATUSES:
        return False, "Trạng thái không hợp lệ."

    conn = connect_db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        booking = conn.execute("""
            SELECT *
            FROM bookings
            WHERE booking_code = ?
        """, (code,)).fetchone()

        if booking is None:
            conn.rollback()
            return False, "Không tìm thấy đơn đặt phòng."

        if new_status == "Đã xác nhận":
            overlap = conn.execute("""
                SELECT COUNT(*)
                FROM bookings
                WHERE room_id = ?
                  AND booking_code != ?
                  AND status IN ('Chờ xác nhận', 'Đã xác nhận')
                  AND checkin < ?
                  AND checkout > ?
            """, (
                booking["room_id"],
                code,
                booking["checkout"],
                booking["checkin"],
            )).fetchone()[0]

            if overlap > 0:
                conn.rollback()
                return False, (
                    "Không thể xác nhận: phòng đã có đơn đặt "
                    "trùng ngày."
                )

        conn.execute("""
            UPDATE bookings
            SET status = ?
            WHERE booking_code = ?
        """, (new_status, code))

        conn.commit()
        return True, "Đã cập nhật trạng thái đặt phòng."

    except Exception as e:
        conn.rollback()
        return False, str(e)

    finally:
        conn.close()


# =========================================================
# 6. CHAT KHÁCH HÀNG - NHÂN VIÊN
# =========================================================

def save_message(code, sender, sender_name, message):
    message = message.strip()

    if not message:
        return False

    conn = connect_db()

    booking = conn.execute("""
        SELECT booking_code
        FROM bookings
        WHERE booking_code = ?
    """, (code.strip().upper(),)).fetchone()

    if not booking:
        conn.close()
        return False

    conn.execute("""
        INSERT INTO messages (
            booking_code, sender, sender_name, message, created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        code.strip().upper(),
        sender,
        sender_name,
        message,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

    conn.commit()
    conn.close()
    return True


# =========================================================
# 7. CHATBOX AI
# =========================================================

def get_api_key():
    try:
        key = st.secrets.get("OPENAI_API_KEY", "")
        if key:
            return key
    except Exception:
        pass

    return os.getenv("OPENAI_API_KEY", "")


def get_ai_client():
    key = get_api_key()

    if not key or OpenAI is None:
        return None

    try:
        return OpenAI(api_key=key)
    except Exception:
        return None


def ai_answer(question):
    rooms = get_rooms()

    if rooms.empty:
        room_context = "Khách sạn chưa có phòng."
    else:
        room_context = "\n".join([
            f"Phòng {r['room_number']}: {r['room_type']}, "
            f"sức chứa {r['capacity']} người, "
            f"giá {r['price']:,.0f} VNĐ/đêm. "
            f"{r['description']}"
            for _, r in rooms.iterrows()
        ])

    client = get_ai_client()

    if client is None:
        q = question.lower()

        if "giá" in q or "phòng" in q or "loại" in q:
            return (
                "Thông tin phòng hiện có:\n\n"
                + room_context
                + "\n\nBạn vui lòng vào mục Đặt phòng để "
                "kiểm tra phòng còn trống theo ngày lưu trú."
            )

        return (
            "Xin chào! Tôi là trợ lý AI của khách sạn. "
            "Tôi có thể hỗ trợ bạn tìm hiểu loại phòng, giá phòng "
            "và cách đặt phòng.\n\n"
            "Đây là chế độ minh họa chưa kết nối AI. "
            "Bạn có thể hỏi về giá phòng hoặc loại phòng."
        )

    system_prompt = f"""
Bạn là trợ lý chăm sóc khách hàng của khách sạn.

Hãy:
- Trả lời bằng tiếng Việt, thân thiện và ngắn gọn.
- Giới thiệu loại phòng, sức chứa, giá phòng theo dữ liệu bên dưới.
- Hướng dẫn khách sử dụng chức năng đặt phòng.
- Không tự xác nhận phòng, không tự tạo đơn đặt phòng.
- Không khẳng định phòng còn trống nếu chưa kiểm tra ngày lưu trú.
- Không tự bịa thông tin về khách sạn.

DỮ LIỆU PHÒNG:
{room_context}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.4,
        )

        return (
            response.choices[0].message.content
            or "Xin lỗi, tôi chưa có câu trả lời."
        )

    except Exception:
        return (
            "Hiện chưa thể kết nối AI. Bạn vui lòng thử lại "
            "hoặc nhắn tin cho nhân viên khách sạn."
        )


# =========================================================
# 8. HÀM TIỆN ÍCH
# =========================================================

def money(value):
    return f"{float(value):,.0f} VNĐ".replace(",", ".")


def safe_date(value):
    if not value:
        return "Chưa cập nhật"
    return str(value)


def show_booking_info(booking):
    st.markdown(f"### 🧾 Đơn đặt phòng {booking['booking_code']}")

    c1, c2 = st.columns(2)

    with c1:
        st.write(f"**Khách hàng:** {booking['customer_name']}")
        st.write(f"**Điện thoại:** {booking['phone']}")
        st.write(f"**Phòng:** {booking['room_number']} - {booking['room_type']}")

    with c2:
        st.write(f"**Nhận phòng:** {booking['checkin']}")
        st.write(f"**Trả phòng:** {booking['checkout']}")
        st.write(f"**Số đêm:** {booking['nights']}")
        st.write(f"**Số khách:** {booking['guests']}")

    st.write(f"**Tổng tiền:** {money(booking['total_price'])}")
    st.write(f"**Trạng thái:** {booking['status']}")


def chat_interface(code, sender, sender_name):
    st.subheader("💬 Trò chuyện")

    messages = get_messages(code)

    if not messages:
        st.info("Chưa có tin nhắn. Hãy gửi lời nhắn đầu tiên.")

    for msg in messages:
        role = "assistant" if msg["sender"] == sender else "user"

        with st.chat_message(role):
            st.caption(
                f"{msg['sender_name']} · {msg['created_at']}"
            )
            st.markdown(msg["message"])

    with st.form(f"chat_form_{sender}_{code}", clear_on_submit=True):
        message = st.text_input(
            "Tin nhắn",
            placeholder="Nhập nội dung trò chuyện...",
        )

        submitted = st.form_submit_button(
            "📨 Gửi tin nhắn",
            use_container_width=True,
        )

        if submitted:
            if message.strip():
                ok = save_message(
                    code, sender, sender_name, message
                )

                if ok:
                    st.rerun()
                else:
                    st.error("Không gửi được tin nhắn.")


# =========================================================
# 9. KHỞI TẠO
# =========================================================

init_db()

if "staff_logged_in" not in st.session_state:
    st.session_state.staff_logged_in = False

if "customer_chat_code" not in st.session_state:
    st.session_state.customer_chat_code = ""

if "ai_history" not in st.session_state:
    st.session_state.ai_history = []


# =========================================================
# 10. SIDEBAR
# =========================================================

with st.sidebar:
    st.markdown("# 🏨 HOTEL BOOKING")
    st.caption("Đặt phòng và chăm sóc khách hàng")

    page = st.radio(
        "MENU",
        [
            "🏠 Trang chủ",
            "🛏️ Đặt phòng",
            "🔎 Tra cứu đặt phòng",
            "💬 Chat với nhân viên",
            "🤖 Chatbox AI",
            "👨‍💼 Quản lý nhân viên",
        ],
    )

    st.divider()
    st.caption("Bản demo sử dụng SQLite.")


# =========================================================
# 11. TRANG CHỦ
# =========================================================

if page == "🏠 Trang chủ":
    st.title("🏨 Chào mừng đến với khách sạn")

    st.markdown(
        "Đặt phòng nhanh chóng, tra cứu đơn và trò chuyện "
        "với nhân viên ngay trên hệ thống."
    )

    rooms = get_rooms()

    c1, c2, c3 = st.columns(3)

    c1.metric("Số loại phòng / phòng", len(rooms))
    c2.metric(
        "Giá phòng thấp nhất",
        money(rooms["price"].min()) if not rooms.empty else "0 VNĐ",
    )
    c3.metric(
        "Sức chứa lớn nhất",
        f"{int(rooms['capacity'].max())} người"
        if not rooms.empty else "0 người",
    )

    st.divider()
    st.subheader("🛏️ Danh sách phòng")

    if rooms.empty:
        st.info("Chưa có phòng.")
    else:
        cols = st.columns(3)

        for index, (_, room) in enumerate(rooms.iterrows()):
            with cols[index % 3]:
                with st.container(border=True):
                    st.markdown(f"### 🚪 Phòng {room['room_number']}")
                    st.markdown(f"**{room['room_type']}**")
                    st.write(f"Sức chứa: {room['capacity']} người")
                    st.markdown(f"### {money(room['price'])}/đêm")
                    st.write(room["description"])


# =========================================================
# 12. TRANG ĐẶT PHÒNG
# =========================================================

elif page == "🛏️ Đặt phòng":
    st.title("🛏️ Đặt phòng khách sạn")

    st.info(
        "Chọn ngày nhận phòng, ngày trả phòng và số khách. "
        "Hệ thống sẽ kiểm tra phòng phù hợp."
    )

    with st.form("booking_form"):
        st.subheader("1. Thông tin lưu trú")

        c1, c2 = st.columns(2)

        with c1:
            checkin = st.date_input(
                "Ngày nhận phòng",
                value=date.today() + timedelta(days=1),
                min_value=date.today(),
            )

        with c2:
            checkout = st.date_input(
                "Ngày trả phòng",
                value=date.today() + timedelta(days=2),
                min_value=date.today() + timedelta(days=1),
            )

        guests = st.number_input(
            "Số khách",
            min_value=1,
            max_value=20,
            value=2,
        )

        st.subheader("2. Thông tin khách hàng")

        customer_name = st.text_input("Họ và tên *")
        phone = st.text_input("Số điện thoại *")
        email = st.text_input("Email")
        note = st.text_area("Yêu cầu đặc biệt")

        submitted = st.form_submit_button(
            "🔍 Kiểm tra phòng và đặt phòng",
            use_container_width=True,
        )

    if submitted:
        if not customer_name.strip() or not phone.strip():
            st.error("Vui lòng nhập họ tên và số điện thoại.")
        elif checkout <= checkin:
            st.error("Ngày trả phòng phải sau ngày nhận phòng.")
        elif checkin < date.today():
            st.error("Ngày nhận phòng không được ở quá khứ.")
        else:
            rooms_available = available_rooms(
                checkin, checkout, int(guests)
            )

            if not rooms_available:
                st.warning(
                    "Không có phòng phù hợp trong khoảng ngày đã chọn. "
                    "Vui lòng đổi ngày hoặc số khách."
                )
            else:
                st.success(
                    f"Tìm thấy {len(rooms_available)} phòng phù hợp."
                )

                options = {
                    (
                        f"Phòng {r['room_number']} - {r['room_type']} | "
                        f"{money(r['price'])}/đêm | "
                        f"Tối đa {r['capacity']} khách"
                    ): r
                    for r in rooms_available
                }

                selected = st.selectbox(
                    "Chọn phòng",
                    list(options.keys()),
                    key="available_room_choice",
                )

                room = options[selected]
                nights = (checkout - checkin).days
                total = float(room["price"]) * nights

                st.markdown(f"### 💰 Tổng tiền dự kiến: {money(total)}")
                st.caption(
                    f"{nights} đêm × {money(room['price'])}/đêm"
                )

                if st.button(
                    "✅ Xác nhận gửi yêu cầu đặt phòng",
                    type="primary",
                    use_container_width=True,
                ):
                    ok, result = create_booking(
                        customer_name,
                        phone,
                        email,
                        room["id"],
                        checkin,
                        checkout,
                        int(guests),
                        note,
                    )

                    if ok:
                        st.success("Đã gửi yêu cầu đặt phòng!")
                        st.markdown(
                            f"## Mã đặt phòng: `{result}`"
                        )
                        st.warning(
                            "Vui lòng lưu mã đặt phòng để tra cứu "
                            "và trò chuyện với nhân viên. "
                            "Đơn đang ở trạng thái Chờ xác nhận."
                        )
                    else:
                        st.error(result)


# =========================================================
# 13. TRA CỨU ĐẶT PHÒNG
# =========================================================

elif page == "🔎 Tra cứu đặt phòng":
    st.title("🔎 Tra cứu đặt phòng")

    code = st.text_input(
        "Nhập mã đặt phòng",
        placeholder="Ví dụ: BK260928ABC123",
    )

    if st.button("🔍 Tra cứu", use_container_width=True):
        booking = get_booking(code)

        if booking:
            show_booking_info(booking)
        else:
            st.error("Không tìm thấy đơn đặt phòng. Vui lòng kiểm tra mã.")


# =========================================================
# 14. CHAT VỚI NHÂN VIÊN
# =========================================================

elif page == "💬 Chat với nhân viên":
    st.title("💬 Chat với nhân viên khách sạn")

    st.info(
        "Nhập mã đặt phòng để mở cuộc trò chuyện. "
        "Tin nhắn sẽ được lưu theo mã đặt phòng."
    )

    code = st.text_input(
        "Mã đặt phòng",
        value=st.session_state.customer_chat_code,
        placeholder="Nhập mã đặt phòng",
    )

    if st.button("Mở cuộc trò chuyện", use_container_width=True):
        booking = get_booking(code)

        if booking:
            st.session_state.customer_chat_code = code.strip().upper()
            st.rerun()
        else:
            st.error("Mã đặt phòng không hợp lệ.")

    code = st.session_state.customer_chat_code

    if code:
        booking = get_booking(code)

        if booking:
            st.success(
                f"Đang trò chuyện về đơn {code} - "
                f"Phòng {booking['room_number']}"
            )

            chat_interface(
                code,
                sender="customer",
                sender_name=booking["customer_name"],
            )
        else:
            st.session_state.customer_chat_code = ""


# =========================================================
# 15. CHATBOX AI
# =========================================================

elif page == "🤖 Chatbox AI":
    st.title("🤖 Trợ lý AI khách sạn")

    st.caption(
        "Hỏi về loại phòng, giá phòng, sức chứa và cách đặt phòng."
    )

    if get_api_key() and OpenAI is not None:
        st.success("Đã cấu hình OpenAI API.")
    else:
        st.info(
            "Chế độ minh họa đang bật. Cấu hình API key để sử dụng AI thật."
        )

    for item in st.session_state.ai_history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    prompt = st.chat_input("Nhập câu hỏi của bạn...")

    if prompt:
        st.session_state.ai_history.append({
            "role": "user",
            "content": prompt,
        })

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("AI đang trả lời..."):
                answer = ai_answer(prompt)
            st.markdown(answer)

        st.session_state.ai_history.append({
            "role": "assistant",
            "content": answer,
        })

    if st.session_state.ai_history:
        if st.button("🧹 Xóa lịch sử AI"):
            st.session_state.ai_history = []
            st.rerun()


# =========================================================
# 16. QUẢN LÝ NHÂN VIÊN
# =========================================================

elif page == "👨‍💼 Quản lý nhân viên":
    st.title("👨‍💼 Khu vực nhân viên")

    if not st.session_state.staff_logged_in:
        st.warning("Vui lòng đăng nhập để quản lý đặt phòng.")

        with st.form("staff_login_form"):
            password = st.text_input(
                "Mật khẩu nhân viên",
                type="password",
            )

            login = st.form_submit_button(
                "🔐 Đăng nhập",
                use_container_width=True,
            )

            if login:
                if password == STAFF_PASSWORD:
                    st.session_state.staff_logged_in = True
                    st.rerun()
                else:
                    st.error("Mật khẩu không chính xác.")

    else:
        if st.button("🚪 Đăng xuất"):
            st.session_state.staff_logged_in = False
            st.rerun()

        st.divider()

        bookings = get_all_bookings()

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Tổng đơn", len(bookings))
        c2.metric(
            "Chờ xác nhận",
            int((bookings["status"] == "Chờ xác nhận").sum())
            if not bookings.empty else 0,
        )
        c3.metric(
            "Đã xác nhận",
            int((bookings["status"] == "Đã xác nhận").sum())
            if not bookings.empty else 0,
        )
        c4.metric(
            "Đã hủy",
            int((bookings["status"] == "Đã hủy").sum())
            if not bookings.empty else 0,
        )

        st.subheader("📋 Danh sách đặt phòng")

        if bookings.empty:
            st.info("Chưa có đơn đặt phòng.")
        else:
            st.dataframe(
                bookings[[
                    "booking_code",
                    "customer_name",
                    "phone",
                    "room_number",
                    "room_type",
                    "checkin",
                    "checkout",
                    "guests",
                    "total_price",
                    "status",
                ]].rename(columns={
                    "booking_code": "Mã đặt phòng",
                    "customer_name": "Khách hàng",
                    "phone": "Điện thoại",
                    "room_number": "Phòng",
                    "room_type": "Loại phòng",
                    "checkin": "Ngày nhận",
                    "checkout": "Ngày trả",
                    "guests": "Số khách",
                    "total_price": "Tổng tiền",
                    "status": "Trạng thái",
                }),
                use_container_width=True,
                hide_index=True,
            )

        st.divider()
        st.subheader("🔄 Xử lý đơn đặt phòng")

        if not bookings.empty:
            booking_options = {
                (
                    f"{r['booking_code']} | Phòng {r['room_number']} | "
                    f"{r['customer_name']} | {r['status']}"
                ): r["booking_code"]
                for _, r in bookings.iterrows()
            }

            selected_label = st.selectbox(
                "Chọn đơn đặt phòng",
                list(booking_options.keys()),
            )

            selected_code = booking_options[selected_label]
            selected_booking = get_booking(selected_code)

            show_booking_info(selected_booking)

            new_status = st.selectbox(
                "Trạng thái mới",
                BOOKING_STATUSES,
                index=BOOKING_STATUSES.index(
                    selected_booking["status"]
                ),
            )

            if st.button(
                "💾 Cập nhật trạng thái",
                use_container_width=True,
            ):
                ok, message = update_booking_status(
                    selected_code, new_status
                )

                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

            st.divider()

            chat_interface(
                selected_code,
                sender="staff",
                sender_name="Nhân viên",
            )


# =========================================================
# 17. CHÂN TRANG
# =========================================================

st.divider()
st.caption(
    "🏨 Hotel Booking | Đặt phòng khách sạn và hỗ trợ khách hàng "
    "| Streamlit + SQLite"
)
