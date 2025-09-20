# -*- coding: utf-8 -*-
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# ================== إعدادات أساسية (عدّل هنا) ==================
API_TOKEN = os.getenv("BOT_TOKEN", "8298120558:AAFA2oXim7IPR900tXqT-T8VS7su9UVpzpk")
OWNER_ID = int(os.getenv("OWNER_ID", "2045209268"))              # آيدي المالك
CHANNEL_ID = os.getenv("CHANNEL_ID", "-2853252241")          # آيدي القناة أو @username
DB_PATH = os.getenv("DB_PATH", "storage.db")
# ===============================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# ================== قاعدة البيانات ==================
def db_connect():
    return sqlite3.connect(DB_PATH)

def db_init():
    with closing(db_connect()) as con, con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            is_registered INTEGER DEFAULT 0,
            is_mod INTEGER DEFAULT 0,
            created_at TEXT
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,                      -- file | image | video | audio | app
            file_id TEXT NOT NULL,
            thumb_id TEXT,
            name TEXT,
            caption TEXT,
            uploader_id INTEGER,
            status TEXT DEFAULT 'active',   -- active | trashed
            channel_msg_id INTEGER,
            created_at TEXT,
            deleted_at TEXT
        )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_items_type_status ON items(type, status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC)")
db_init()

# ================== الأدوات المساعدة ==================
CAT_TYPES = ["file", "image", "video", "audio", "app"]

def now_str():
    return datetime.utcnow().isoformat(timespec='seconds')

def user_is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def user_is_mod(uid: int) -> bool:
    with closing con:
        row = con.execute("SELECT is_mod FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0]) or user_is_owner(uid)

def ensure_user(u: types.User):
    with closing(db_connect()) as con, con:
        exists = con.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO users(user_id, full_name, is_registered, is_mod, created_at) VALUES(?,?,?,?,?)",
                (u.id, u.full_name, 0, 0, now_str())
            )

def register_user(uid: int):
    with closing(db_connect()) as con, con:
        con.execute("UPDATE users SET is_registered=1 WHERE user_id=?", (uid,))

def user_is_registered(uid: int) -> bool:
    with closing(db_connect()) as con:
        row = con.execute("SELECT is_registered FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0])

def infer_doc_type(doc: types.Document) -> str:
    # apps/برامج: ملفات EXE, APK, DMG, MSI, etc.
    if doc.mime_type:
        mt = doc.mime_type.lower()
        if "application/vnd.android.package-archive" in mt or mt.endswith("/x-msdownload"):
            return "app"
        if mt.startswith("audio/"):
            return "audio"
        if mt.startswith("video/"):
            return "video"
        if mt.startswith("image/"):
            return "image"
        if mt in ("application/x-msdownload", "application/x-dosexec"):
            return "app"
    # بالاسم:
    name = (doc.file_name or "").lower()
    if any(name.endswith(ext) for ext in [".apk", ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm"]):
        return "app"
    return "file"

def send_main_menu(is_owner: bool = False) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📁 ملفات", callback_data="cat:open:file"),
         InlineKeyboardButton("🖼️ صور", callback_data="cat:open:image")],
        [InlineKeyboardButton("🎥 فيديوهات", callback_data="cat:open:video"),
         InlineKeyboardButton("🎵 صوتيات", callback_data="cat:open:audio")],
        [InlineKeyboardButton("💻 تطبيقات / برامج", callback_data="cat:open:app")],
        [InlineKeyboardButton("🔎 بحث", callback_data="search:open"),
         InlineKeyboardButton("🗑️ سلة المحذوفات", callback_data="trash:list:1")],
        [InlineKeyboardButton("👤 حسابي", callback_data="user:profile")]
    ]
    if is_owner:
        kb.append([InlineKeyboardButton("🛠️ إدارة الأزرار", callback_data="admin:open")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def category_menu(cat_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📂 عرض الملفات", callback_data=f"cat:list:{cat_type}:1")],
        [InlineKeyboardButton("⬆️ رفع ملف جديد", callback_data=f"cat:upload:{cat_type}")],
        [InlineKeyboardButton("🆕 المضافة مؤخرًا", callback_data=f"cat:list:{cat_type}:recent")],
        [InlineKeyboardButton("🔎 بحث في الفئة", callback_data=f"search:cat:{cat_type}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main:open"),
         InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open")]
    ])

def list_nav(cat_type: str, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    row = []
    if has_prev:
        row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"nav:page:{cat_type}:{page-1}"))
    if has_next:
        row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"nav:page:{cat_type}:{page+1}"))
    rows = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"cat:open:{cat_type}"),
                 InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def item_actions(item_id: int, in_trash: bool = False, owner_or_mod: bool = False) -> InlineKeyboardMarkup:
    kb = []
    if not in_trash:
        kb.append([InlineKeyboardButton("✏️ تعديل", callback_data=f"item:edit:{item_id}"),
                   InlineKeyboardButton("🗑️ حذف", callback_data=f"item:del:{item_id}")])
    else:
        kb.append([InlineKeyboardButton("♻️ استرجاع", callback_data=f"trash:restore:{item_id}")])
        if owner_or_mod:
            kb.append([InlineKeyboardButton("❌ حذف نهائي", callback_data=f"trash:purge:{item_id}")])
    kb.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================== حالات FSM ==================
class UploadWait(StatesGroup):
    for_type = State()

class EditWait(StatesGroup):
    new_name = State()
    new_caption = State()
    choice = State()

# ================== أوامر البداية والتسجيل ==================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    ensure_user(message.from_user)
    text = "👋 أهلاً بك في بوت التخزين السحابي.\n"
    if user_is_owner(message.from_user.id):
        text += "أنت المالك. لديك صلاحيات كاملة."
    text += "\n\nاضغط لبدء الاستخدام:"
    btns = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ تسجيل حساب", callback_data="user:register")
    )
    btns.add(InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main:open"))
    await message.answer(text, reply_markup=btns)

@dp.callback_query_handler(lambda c: c.data == "user:register")
async def cb_register(call: CallbackQuery):
    ensure_user(call.from_user)
    register_user(call.from_user.id)
    await call.message.edit_text("✅ تم تسجيل حسابك بنجاح.\nاستخدم الأزرار للتنقل.", reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer("تم")

@dp.callback_query_handler(lambda c: c.data == "user:profile")
async def cb_profile(call: CallbackQuery):
    ensure_user(call.from_user)
    reg = user_is_registered(call.from_user.id)
    role = "مالك" if user_is_owner(call.from_user.id) else ("مشرف" if user_is_mod(call.from_user.id) else "مستخدم")
    txt = f"👤 حسابي\n\nالاسم: {call.from_user.full_name}\nالحالة: {'مسجل' if reg else 'غير مسجل'}\nالدور: {role}"
    await call.message.edit_text(txt, reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer()

# ================== القائمة الرئيسية والفئات ==================
@dp.callback_query_handler(lambda c: c.data == "main:open")
async def cb_main(call: CallbackQuery):
    await call.message.edit_text("🏠 القائمة الرئيسية", reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("cat:open:"))
async def cb_open_cat(call: CallbackQuery):
    cat = call.data.split(":")[2]
    if cat not in CAT_TYPES:
        return await call.answer("فئة غير معروفة.", show_alert=True)
    await call.message.edit_text(f"🔎 الفئة: {cat}", reply_markup=category_menu(cat))
    await call.answer()

# ================== عرض القوائم مع ترقيم ==================
PAGE_SIZE = 6

def fetch_items(cat_type: str, page: int) -> Tuple[list, bool, bool]:
    offset = (page - 1) * PAGE_SIZE
    with closing(db_connect()) as con:
        rows = con.execute("""
            SELECT id, name, caption, file_id, type FROM items
            WHERE status='active' AND type=?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (cat_type, PAGE_SIZE + 1, offset)).fetchall()
    has_next = len(rows) > PAGE_SIZE
    items = rows[:PAGE_SIZE]
    has_prev = page > 1
    return items, has_prev, has_next

@dp.callback_query_handler(lambda c: c.data.startswith("cat:list:"))
async def cb_list_cat(call: CallbackQuery):
    _, _, cat_type, page = call.data.split(":")
    page = 1 if page == "recent" else int(page)
    items, has_prev, has_next = fetch_items(cat_type, page)
    if not items:
        await call.message.edit_text("لا توجد عناصر بعد في هذه الفئة.", reply_markup=category_menu(cat_type))
        return await call.answer()
    # نبني قائمة مختصرة بأزرار لعناصر فردية
    kb = InlineKeyboardMarkup(row_width=2)
    for it in items:
        it_id, name, caption, file_id, t = it
        title = name or (caption[:20] + "…") if caption else f"{t} #{it_id}"
        kb.insert(InlineKeyboardButton(f"📦 {title}", callback_data=f"item:view:{it_id}"))
    # تنقل
    nav = list_nav(cat_type, page, has_prev, has_next)
    kb.inline_keyboard.extend(nav.inline_keyboard)
    await call.message.edit_text(f"📂 عناصر الفئة: {cat_type} (صفحة {page})", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("nav:page:"))
async def cb_nav_page(call: CallbackQuery):
    _, _, cat_type, page = call.data.split(":")
    call.data = f"cat:list:{cat_type}:{page}"
    return await cb_list_cat(call)

# ================== عرض عنصر وتحرير/حذف ==================
def get_item(item_id: int):
    with closing(db_connect()) as con:
        return con.execute("SELECT id, type, file_id, thumb_id, name, caption, uploader_id, status, channel_msg_id FROM items WHERE id=?", (item_id,)).fetchone()

@dp.callback_query_handler(lambda c: c.data.startswith("item:view:"))
async def cb_item_view(call: CallbackQuery):
    item_id = int(call.data.split(":")[2])
    row = get_item(item_id)
    if not row:
        await call.answer("العنصر غير موجود.", show_alert=True)
        return
    id_, t, file_id, thumb, name, caption, uploader, status, _ = row
    txt = f"📦 عنصر #{id_}\nالنوع: {t}\nالاسم: {name or '-'}\nالوصف: {caption or '-'}\nالرافع: {uploader}"
    in_trash = (status == "trashed")
    kb = item_actions(id_, in_trash=in_trash, owner_or_mod=user_is_mod(call.from_user.id))
    await call.message.edit_text(txt, reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("item:del:"))
async def cb_item_del(call: CallbackQuery):
    item_id = int(call.data.split(":")[2])
    row = get_item(item_id)
    if not row:
        return await call.answer("غير موجود.", show_alert=True)
    # لا نطلب صلاحية خاصة للحذف للسلة، لكن يمكن تخصيصها لاحقًا
    with closing(db_connect()) as con, con:
        con.execute("UPDATE items SET status='trashed', deleted_at=? WHERE id=?", (now_str(), item_id))
    await call.message.edit_text("🗑️ تم نقل العنصر إلى سلة المحذوفات.", reply_markup=InlineKeyboardMarkup().add(
        InlineKeyboardButton("اذهب للسلة", callback_data="trash:list:1"),
    ).add(InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open")))
    await call.answer("تم الحذف")

@dp.callback_query_handler(lambda c: c.data.startswith("item:edit:"))
async def cb_item_edit(call: CallbackQuery, state: FSMContext):
    item_id = int(call.data.split(":")[2])
    await state.update_data(edit_id=item_id)
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✏️ تعديل الاسم", callback_data="edit:name"),
    ).add(InlineKeyboardButton("📝 تعديل الوصف", callback_data="edit:caption")).add(
        InlineKeyboardButton("🔙 رجوع", callback_data=f"item:view:{item_id}")
    )
    await call.message.edit_text("اختر ما تريد تعديله:", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data in ("edit:name", "edit:caption"))
async def cb_edit_choice(call: CallbackQuery, state: FSMContext):
    await state.update_data(choice=call.data.split(":")[1])
    if call.data.endswith("name"):
        await EditWait.new_name.set()
        await call.message.edit_text("أرسل الاسم الجديد الآن:")
    else:
        await EditWait.new_caption.set()
        await call.message.edit_text("أرسل الوصف الجديد الآن:")
    await call.answer()

@dp.message_handler(state=EditWait.new_name, content_types=types.ContentType.TEXT)
async def on_new_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data["edit_id"]
    with closing(db_connect()) as con, con:
        con.execute("UPDATE items SET name=? WHERE id=?", (message.text.strip(), item_id))
    await message.answer("✅ تم تحديث الاسم.", reply_markup=send_main_menu(user_is_owner(message.from_user.id)))
    await state.finish()

@dp.message_handler(state=EditWait.new_caption, content_types=types.ContentType.TEXT)
async def on_new_caption(message: types.Message, state: FSMContext):
    data = await state.get_data()
    item_id = data["edit_id"]
    with closing(db_connect()) as con, con:
        con.execute("UPDATE items SET caption=? WHERE id=?", (message.text.strip(), item_id))
    await message.answer("✅ تم تحديث الوصف.", reply_markup=send_main_menu(user_is_owner(message.from_user.id)))
    await state.finish()

# ================== السلة: عرض/استرجاع/حذف نهائي ==================
def fetch_trash(page: int) -> Tuple[list, bool, bool]:
    offset = (page - 1) * PAGE_SIZE
    with closing(db_connect()) as con:
        rows = con.execute("""
            SELECT id, name, caption, type FROM items
            WHERE status='trashed'
            ORDER BY deleted_at DESC
            LIMIT ? OFFSET ?
        """, (PAGE_SIZE + 1, offset)).fetchall()
    has_next = len(rows) > PAGE_SIZE
    items = rows[:PAGE_SIZE]
    has_prev = page > 1
    return items, has_prev, has_next

@dp.callback_query_handler(lambda c: c.data.startswith("trash:list:"))
async def cb_trash_list(call: CallbackQuery):
    page = int(call.data.split(":")[2])
    items, has_prev, has_next = fetch_trash(page)
    kb = InlineKeyboardMarkup(row_width=2)
    if not items:
        kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open"))
        return await call.message.edit_text("السلة فارغة.", reply_markup=kb)
    for it in items:
        it_id, name, caption, t = it
        title = name or (caption[:20] + "…") if caption else f"{t} #{it_id}"
        kb.insert(InlineKeyboardButton(f"🗑️ {title}", callback_data=f"item:view:{it_id}"))
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"trash:list:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"trash:list:{page+1}"))
    if nav:
        kb.row(*nav)
    # أزرار المالك
    if user_is_mod(call.from_user.id):
        kb.row(InlineKeyboardButton("🧹 تفريغ الكل", callback_data="trash:purge_all:confirm"))
    kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open"))
    await call.message.edit_text(f"🗑️ سلة المحذوفات (صفحة {page})", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("trash:restore:"))
async def cb_trash_restore(call: CallbackQuery):
    item_id = int(call.data.split(":")[2])
    with closing(db_connect()) as con, con:
        con.execute("UPDATE items SET status='active', deleted_at=NULL WHERE id=?", (item_id,))
    await call.message.edit_text("♻️ تم استرجاع العنصر.", reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer("تم الاسترجاع")

@dp.callback_query_handler(lambda c: c.data.startswith("trash:purge:"))
async def cb_trash_purge(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("صلاحية غير كافية.", show_alert=True)
    item_id = int(call.data.split(":")[2])
    with closing(db_connect()) as con, con:
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
    await call.message.edit_text("❌ تم حذف العنصر نهائيًا.", reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer("تم الحذف النهائي")

@dp.callback_query_handler(lambda c: c.data == "trash:purge_all:confirm")
async def cb_trash_purge_all(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("صلاحية غير كافية.", show_alert=True)
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⚠️ تأكيد التفريغ", callback_data="trash:purge_all:do")
    ).add(InlineKeyboardButton("إلغاء", callback_data="trash:list:1"))
    await call.message.edit_text("ستقوم بحذف جميع عناصر السلة نهائيًا. هل أنت متأكد؟", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "trash:purge_all:do")
async def cb_trash_purge_all_do(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("صلاحية غير كافية.", show_alert=True)
    with closing(db_connect()) as con, con:
        con.execute("DELETE FROM items WHERE status='trashed'")
    await call.message.edit_text("🧹 تم تفريغ السلة نهائيًا.", reply_markup=send_main_menu(user_is_owner(call.from_user.id)))
    await call.answer("تم")

# ================== رفع جديد (حسب الفئة) ==================
@dp.callback_query_handler(lambda c: c.data.startswith("cat:upload:"))
async def cb_upload_prompt(call: CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[2]
    if not user_is_registered(call.from_user.id):
        return await call.answer("سجّل أولاً: /start", show_alert=True)
    await state.update_data(upload_for=cat)
    await UploadWait.for_type.set()
    await call.message.edit_text(f"أرسل الآن العنصر لرفعه ضمن فئة: {cat}\n(صورة/فيديو/ملف/صوت بحسب الفئة)")
    await call.answer()

async def store_to_channel_and_db(
    msg: types.Message,
    cat: str,
    file_id: str,
    thumb_id: Optional[str],
    name: Optional[str],
    caption: Optional[str]
):
    # إرسال للقناة وفق النوع
    sent = None
    if cat == "image" and msg.photo:
        sent = await bot.send_photo(CHANNEL_ID, photo=file_id, caption=caption)
    elif cat == "video" and (getattr(msg, "video", None) or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
        if getattr(msg, "video", None):
            sent = await bot.send_video(CHANNEL_ID, video=file_id, caption=caption, thumb=thumb_id)
        else:
            sent = await bot.send_document(CHANNEL_ID, document=file_id, caption=caption, thumb=thumb_id)
    elif cat == "audio" and (getattr(msg, "audio", None) or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio/"))):
        if getattr(msg, "audio", None):
            sent = await bot.send_audio(CHANNEL_ID, audio=file_id, caption=caption, thumb=thumb_id)
        else:
            sent = await bot.send_document(CHANNEL_ID, document=file_id, caption=caption, thumb=thumb_id)
    else:
        # file/app أو أي وثيقة
        sent = await bot.send_document(CHANNEL_ID, document=file_id, caption=caption, thumb=thumb_id)
    with closing(db_connect()) as con, con:
        con.execute("""
            INSERT INTO items(type, file_id, thumb_id, name, caption, uploader_id, status, channel_msg_id, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (cat, file_id, thumb_id, name, caption, msg.from_user.id, "active", sent.message_id if sent else None, now_str()))

def detect_category_from_message(message: types.Message) -> Tuple[str, str, Optional[str], Optional[str]]:
    # return (cat, file_id, thumb_id, name)
    if message.photo:
        return "image", message.photo[-1].file_id, None, None
    if message.video:
        thumb = message.video.thumb.file_id if message.video.thumb else None
        return "video", message.video.file_id, thumb, None
    if message.audio:
        thumb = message.audio.thumb.file_id if message.audio.thumb else None
        return "audio", message.audio.file_id, thumb, message.audio.file_name
    if message.document:
        doc = message.document
        t = infer_doc_type(doc)
        return t, doc.file_id, (doc.thumb.file_id if doc.thumb else None), doc.file_name
    raise ValueError("Unsupported content")

@dp.message_handler(state=UploadWait.for_type, content_types=types.ContentType.ANY)
async def on_upload_any(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cat = data.get("upload_for")
    try:
        # تأكد من النوع
        det_cat, file_id, thumb_id, name = detect_category_from_message(message)
        if cat != det_cat and not (cat in ("file", "app") and det_cat == "file"):
            return await message.answer(f"الوسائط لا تتطابق مع فئة {cat}. أعد الإرسال بالصيغة الصحيحة.")
        caption = (message.caption or "").strip() or None
        await store_to_channel_and_db(message, cat, file_id, thumb_id, name, caption)
        await message.answer("✅ تم الرفع وتخزين العنصر في القناة.", reply_markup=send_main_menu(user_is_owner(message.from_user.id)))
        await state.finish()
    except Exception:
        await message.answer("⚠️ لم أتمكن من قراءة هذا النوع. أرسل صورة/فيديو/صوت/ملف مناسب للفئة.")

# ================== دعم الرفع السريع بدون اختيار فئة ==================
@dp.message_handler(content_types=[
    types.ContentType.DOCUMENT,
    types.ContentType.PHOTO,
    types.ContentType.VIDEO,
    types.ContentType.AUDIO
])
async def quick_upload(message: types.Message):
    ensure_user(message.from_user)
    if not user_is_registered(message.from_user.id):
        return await message.answer("ℹ️ سجّل أولاً عبر /start ثم اضغط ✅ تسجيل حساب.")
    det_cat, file_id, thumb_id, name = detect_category_from_message(message)
    caption = (message.caption or "").strip() or None
    await store_to_channel_and_db(message, det_cat, file_id, thumb_id, name, caption)
    await message.answer(f"✅ تم الرفع إلى فئة: {det_cat}", reply_markup=send_main_menu(user_is_owner(message.from_user.id)))

# ================== البحث ==================
def search_items(keyword: str, cat: Optional[str] = None):
    kw = f"%{keyword.lower()}%"
    with closing(db_connect()) as con:
        if cat:
            rows = con.execute("""
                SELECT id, type, name, caption FROM items
                WHERE status='active' AND type=? AND (LOWER(COALESCE(name,'')) LIKE ? OR LOWER(COALESCE(caption,'')) LIKE ?)
                ORDER BY created_at DESC LIMIT 25
            """, (cat, kw, kw)).fetchall()
        else:
            rows = con.execute("""
                SELECT id, type, name, caption FROM items
                WHERE status='active' AND (LOWER(COALESCE(name,'')) LIKE ? OR LOWER(COALESCE(caption,'')) LIKE ?)
                ORDER BY created_at DESC LIMIT 25
            """, (kw, kw)).fetchall()
    return rows

class SearchWait(StatesGroup):
    global_kw = State()
    cat_kw = State()

@dp.callback_query_handler(lambda c: c.data == "search:open")
async def cb_search_open(call: CallbackQuery, state: FSMContext):
    await SearchWait.global_kw.set()
    await call.message.edit_text("🔎 أرسل كلمة البحث الآن (بحث عام):")
    await call.answer()

@dp.message_handler(state=SearchWait.global_kw, content_types=types.ContentType.TEXT)
async def on_search_global(message: types.Message, state: FSMContext):
    kw = message.text.strip()
    rows = search_items(kw)
    if not rows:
        await message.answer("لا نتائج.")
        await state.finish()
        return
    kb = InlineKeyboardMarkup(row_width=2)
    for it in rows:
        it_id, t, name, cap = it
        title = name or (cap[:20] + "…") if cap else f"{t} #{it_id}"
        kb.insert(InlineKeyboardButton(f"{t} | {title}", callback_data=f"item:view:{it_id}"))
    kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open"))
    await message.answer(f"نتائج البحث عن: {kw}", reply_markup=kb)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("search:cat:"))
async def cb_search_cat(call: CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[2]
    await state.update_data(cat=cat)
    await SearchWait.cat_kw.set()
    await call.message.edit_text(f"🔎 أرسل كلمة البحث لفئة: {cat}")
    await call.answer()

@dp.message_handler(state=SearchWait.cat_kw, content_types=types.ContentType.TEXT)
async def on_search_cat(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cat = data.get("cat")
    kw = message.text.strip()
    rows = search_items(kw, cat)
    if not rows:
        await message.answer("لا نتائج ضمن الفئة.")
        await state.finish()
        return
    kb = InlineKeyboardMarkup(row_width=2)
    for it in rows:
        it_id, t, name, cap = it
        title = name or (cap[:20] + "…") if cap else f"{t} #{it_id}"
        kb.insert(InlineKeyboardButton(f"{title}", callback_data=f"item:view:{it_id}"))
    kb.add(InlineKeyboardButton("🏠 الرئيسية", callback_data="main:open"))
    await message.answer(f"نتائج البحث ضمن {cat}: {kw}", reply_markup=kb)
    await state.finish()

# ================== لوحة الإدارة الأساسية ==================
@dp.callback_query_handler(lambda c: c.data == "admin:open")
async def cb_admin_open(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("غير مسموح.", show_alert=True)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("👥 المستخدمون", callback_data="admin:users:1"))
    kb.add(InlineKeyboardButton("📊 إحصاءات", callback_data="admin:stats"))
    kb.add(InlineKeyboardButton("⚙️ إعدادات القناة", callback_data="admin:settings"))
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="main:open"))
    await call.message.edit_text("🛠️ لوحة الإدارة", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:users:"))
async def cb_admin_users(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("غير مسموح.", show_alert=True)
    page = int(call.data.split(":")[2])
    offset = (page - 1) * PAGE_SIZE
    with closing(db_connect()) as con:
        rows = con.execute("""
            SELECT user_id, full_name, is_registered, is_mod FROM users
            ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (PAGE_SIZE + 1, offset)).fetchall()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    kb = InlineKeyboardMarkup(row_width=1)
    if not rows:
        kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:open"))
        return await call.message.edit_text("لا مستخدمين.", reply_markup=kb)
    text = "👥 المستخدمون:\n"
    for u in rows:
        uid, fn, reg, mod = u
        text += f"- {fn} ({uid}) | {'مسجل' if reg else 'غير مسجل'} | {'مشرف' if mod else 'عضو'}\n"
        if user_is_owner(call.from_user.id) and uid != OWNER_ID:
            toggle = "admin:toggle_mod:{}".format(uid)
            kb.add(InlineKeyboardButton(f"{'إلغاء' if mod else 'تعيين'} مشرف: {uid}", callback_data=toggle))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"admin:users:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"admin:users:{page+1}"))
    if nav:
        kb.row(*nav)
    kb.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:open"))
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("admin:toggle_mod:"))
async def cb_admin_toggle_mod(call: CallbackQuery):
    if not user_is_owner(call.from_user.id):
        return await call.answer("فقط المالك.", show_alert=True)
    uid = int(call.data.split(":")[2])
    if uid == OWNER_ID:
        return await call.answer("هذا هو المالك.", show_alert=True)
    with closing(db_connect()) as con:
        row = con.execute("SELECT is_mod FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return await call.answer("المستخدم غير موجود.", show_alert=True)
        new_val = 0 if row[0] else 1
        with con:
            con.execute("UPDATE users SET is_mod=? WHERE user_id=?", (new_val, uid))
    await call.answer("تم التبديل.")
    await cb_admin_users(call)

@dp.callback_query_handler(lambda c: c.data == "admin:stats")
async def cb_admin_stats(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("غير مسموح.", show_alert=True)
    with closing(db_connect()) as con:
        total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM items WHERE status='active'").fetchone()[0]
        trashed = con.execute("SELECT COUNT(*) FROM items WHERE status='trashed'").fetchone()[0]
    await call.message.edit_text(f"📊 الإحصاءات\n\nإجمالي العناصر: {total}\nالنشطة: {active}\nفي السلة: {trashed}",
                                 reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:open")))
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "admin:settings")
async def cb_admin_settings(call: CallbackQuery):
    if not user_is_mod(call.from_user.id):
        return await call.answer("غير مسموح.", show_alert=True)
    txt = f"⚙️ إعدادات القناة\nالقناة الحالية: {CHANNEL_ID}\nتأكد أن البوت مشرف."
    await call.message.edit_text(txt, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data="admin:open")))
    await call.answer()

# ================== أمان بسيط: رفض الأوامر إن لم يُسجل ==================
@dp.message_handler(commands=['admin'])
async def cmd_admin_legacy(message: types.Message):
    if not user_is_mod(message.from_user.id):
        return await message.answer("🚫 هذا الأمر للمشرفين.")
    await message.answer("افتح لوحة الإدارة من الأزرار: 🛠️ إدارة الأزرار")

# ================== بدء التشغيل ==================
if __name__ == "__main__":
    if API_TOKEN == "ضع_توكن_البوت_هنا":
        raise SystemExit("رجاء ضع توكن البوت في API_TOKEN أو BOT_TOKEN env.")
    executor.start_polling(dp, skip_updates=True)