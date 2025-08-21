# === মডিউল ইম্পোর্ট ===
import os
import time
import asyncio
import re
import random

# --- নতুন ডাটাবেস লাইব্রেরি ---
from sqlalchemy import create_engine, Column, Integer, String, Float, func
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError

# --- টেলিগ্রাম লাইব্রেরি ---
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler, CallbackQueryHandler
)
from telegram.error import Forbidden

# --- টেলিগ্রাম ইউজার ক্লায়েন্ট লাইব্রেরি ---
from telethon.sync import TelegramClient, events

# === কনফিগারেশন ===
# এই সকল তথ্য Render-এর Environment Variables থেকে লোড হবে
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID"))
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
DATABASE_URL = os.environ.get("DATABASE_URL")  # Render স্বয়ংক্রিয়ভাবে এটি দেবে
SESSION_NAME = "my_user_session"

# --- অন্যান্য সেটিংস ---
COOLDOWN_SECONDS = 15
BALANCE_PER_OTP = 0.60
BROADCAST_SLEEP_TIME = 0.1
USD_TO_BDT_RATE = 110.0

# --- মেনু বাটন এবং কনভার্সেশন স্টেটস ---
BTN_GET_NUMBER = "🔢 Get Number"
BTN_ACCOUNT = "👤 Account"
BTN_BALANCE = "💰 Balance"
BTN_WITHDRAW = "💸 Withdraw"
CHOOSE_METHOD, ENTER_DETAILS, CONFIRM_WITHDRAW = range(3)
MIN_WITHDRAW = {
    'recharge': 20,
    'rocket': 30,
    'binance': 0.25
}

# === নতুন SQLAlchemy ডাটাবেস সেটআপ ===
# Render-এর PostgreSQL ডাটাবেসের সাথে সংযোগ স্থাপন
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- ডাটাবেসের টেবিলের গঠন (Schema) নির্ধারণ ---
class Number(Base):
    __tablename__ = "numbers"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(String, unique=True, nullable=False)

class UserCooldown(Base):
    __tablename__ = "user_cooldown"
    user_id = Column(Integer, primary_key=True, index=True)
    last_request_time = Column(Float, nullable=False)

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, index=True)

class UserBalance(Base):
    __tablename__ = "user_balances"
    user_id = Column(Integer, primary_key=True, index=True)
    balance = Column(Float, default=0.0)

class ActiveAssignment(Base):
    __tablename__ = "active_assignments"
    number = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    timestamp = Column(Float, nullable=False)

def setup_database():
    """ডাটাবেস এবং টেবিল তৈরি করে।"""
    Base.metadata.create_all(bind=engine)

# --- Helper ফাংশন: নম্বর পরিষ্কার করা ---
def clean_phone_number(raw_number: str) -> str:
    return re.sub(r'\D', '', raw_number)

# === ডাটাবেস ফাংশন (SQLAlchemy দিয়ে নতুন করে লেখা) ===
def get_db_session():
    """একটি ডাটাবেস সেশন তৈরি করে এবং কাজ শেষে বন্ধ করে।"""
    db = SessionLocal()
    try:
        return db
    finally:
        db.close()

def add_or_update_user(user_id):
    db = get_db_session()
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        db.add(User(user_id=user_id))
    user_balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
    if not user_balance:
        db.add(UserBalance(user_id=user_id, balance=0.0))
    db.commit()
    db.close()

def get_user_balance(user_id):
    db = get_db_session()
    user_balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
    db.close()
    return user_balance.balance if user_balance else 0.0

def update_user_balance(user_id, amount):
    db = get_db_session()
    user_balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
    if user_balance:
        user_balance.balance += amount
        db.commit()
    db.close()

def assign_number_to_user(cleaned_number, user_id):
    db = get_db_session()
    # আগের অ্যাসাইনমেন্ট থাকলে মুছে ফেলা
    db.query(ActiveAssignment).filter(ActiveAssignment.user_id == user_id).delete()
    assignment = ActiveAssignment(number=cleaned_number, user_id=user_id, timestamp=time.time())
    db.add(assignment)
    db.commit()
    db.close()

def get_assigned_user(cleaned_number):
    db = get_db_session()
    assignment = db.query(ActiveAssignment).filter(ActiveAssignment.number == cleaned_number).first()
    db.close()
    return assignment.user_id if assignment else None

def remove_assignment(cleaned_number):
    db = get_db_session()
    db.query(ActiveAssignment).filter(ActiveAssignment.number == cleaned_number).delete()
    db.commit()
    db.close()

def add_numbers_to_db(number_list):
    db = get_db_session()
    added_count = 0
    for num in number_list:
        try:
            db.add(Number(number=num))
            db.commit()
            added_count += 1
        except IntegrityError:
            db.rollback()
    db.close()
    return added_count

def delete_number_from_db(number_to_delete):
    db = get_db_session()
    deleted_count = db.query(Number).filter(Number.number == number_to_delete).delete()
    db.commit()
    db.close()
    return deleted_count > 0

def clear_all_numbers_from_db():
    db = get_db_session()
    count = db.query(Number).count()
    db.query(Number).delete()
    db.commit()
    db.close()
    return count

def get_total_numbers_count():
    db = get_db_session()
    count = db.query(Number).count()
    db.close()
    return count

def get_all_user_ids():
    db = get_db_session()
    user_ids = [item.user_id for item in db.query(User.user_id).all()]
    db.close()
    return user_ids

# === বট হ্যান্ডলার ফাংশন ===

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_update_user(user.id)
    keyboard = [[KeyboardButton(BTN_GET_NUMBER)], [KeyboardButton(BTN_ACCOUNT), KeyboardButton(BTN_BALANCE)], [KeyboardButton(BTN_WITHDRAW)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"Hi👋, {user.first_name}!\n\n📞 নাম্বার পেতে Get Number-এ ক্লিক করুন।", reply_markup=reply_markup)
    if user.id == ADMIN_USER_ID:
        await update.message.reply_text("আপনি এই বটের অ্যাডমিন।\n`/add`, `/delete`, `/clearall`, `/stats`, `/broadcast` কমান্ডগুলো ব্যবহার করুন।")

async def handle_get_number_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_or_update_user(user_id)
    db = get_db_session()
    try:
        # Cooldown চেক
        cooldown = db.query(UserCooldown).filter(UserCooldown.user_id == user_id).first()
        current_time = time.time()
        if cooldown and (current_time - cooldown.last_request_time < COOLDOWN_SECONDS):
            await update.message.reply_text(f"অনুগ্রহ করে {int(COOLDOWN_SECONDS - (current_time - cooldown.last_request_time))} সেকেন্ড অপেক্ষা করুন।")
            return

        # ডাটাবেস থেকে একটি দৈবচয়ন নম্বর নেওয়া
        total_numbers = db.query(Number).count()
        if total_numbers > 0:
            random_offset = random.randint(0, total_numbers - 1)
            number_row = db.query(Number).offset(random_offset).first()
        else:
            number_row = None

        if number_row:
            number_to_give = number_row.number
            db.query(Number).filter(Number.id == number_row.id).delete()
            
            # Cooldown আপডেট করা
            if cooldown:
                cooldown.last_request_time = current_time
            else:
                db.add(UserCooldown(user_id=user_id, last_request_time=current_time))
            db.commit()

            cleaned_number = clean_phone_number(number_to_give)
            assign_number_to_user(cleaned_number, user_id)
            
            keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_button")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"📞 আপনার নাম্বার: `{number_to_give}`\n\n🔐 এই নাম্বারে OTP এলে এখানেই পাবেন।\n🚫 না এলে অন্য নাম্বার ট্রাই করুন।\n\n💸 প্রতি OTP-তে আপনার ব্যালেন্সে 0.60 টাকা যোগ হবে।\n💳 20 টাকা হলেই Withdraw করা যাবে।",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text("দুঃখিত, এই মুহূর্তে কোনো নাম্বার অবশিষ্ট নেই।")
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🚨 সতর্কবার্তা: বট-এর সকল নাম্বার শেষ হয়ে গেছে! অনুগ্রহ করে দ্রুত নতুন নাম্বার যোগ করুন।")
    finally:
        db.close()

async def refresh_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(text="🔄 Refreshing...", show_alert=False)

async def handle_account_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_update_user(user.id)
    await update.message.reply_text(f"👤 **Account Info**\n\n- **Name:** {user.full_name}\n- **User ID:** `{user.id}`", parse_mode='Markdown')

async def handle_balance_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_or_update_user(user_id)
    balance = get_user_balance(user_id)
    await update.message.reply_text(f"💰 আপনার বর্তমান ব্যালেন্স: **{balance:.2f}** টাকা।", parse_mode='Markdown')

# --- উইথড্র সিস্টেম ---
async def handle_withdraw_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_or_update_user(user_id)
    balance = get_user_balance(user_id)
    keyboard = [
        [InlineKeyboardButton(f"📱 Mobile Recharge (min {MIN_WITHDRAW['recharge']} টাকা)", callback_data='withdraw_recharge')],
        [InlineKeyboardButton(f"🚀 Rocket (min {MIN_WITHDRAW['rocket']} টাকা)", callback_data='withdraw_rocket')],
        [InlineKeyboardButton(f"🔶 Binance (min {MIN_WITHDRAW['binance']} USD)", callback_data='withdraw_binance')],
        [InlineKeyboardButton("❌ Cancel", callback_data='withdraw_cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"আপনার বর্তমান ব্যালেন্স: **{balance:.2f}** টাকা।\n\nআপনি কোন মাধ্যমে টাকা তুলতে চান?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return CHOOSE_METHOD

async def choose_withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    if choice == 'cancel':
        await query.edit_message_text("উইথড্র বাতিল করা হয়েছে।")
        return ConversationHandler.END
    context.user_data['withdraw_method'] = choice
    min_amount_bdt = MIN_WITHDRAW[choice] if choice != 'binance' else MIN_WITHDRAW[choice] * USD_TO_BDT_RATE
    user_balance = get_user_balance(query.from_user.id)
    if user_balance < min_amount_bdt:
        await query.edit_message_text(f"❌ দুঃখিত, আপনার ব্যালেন্স পর্যাপ্ত নয়। এই মাধ্যমে টাকা তুলতে সর্বনিম্ন ৳{min_amount_bdt:.2f} প্রয়োজন।")
        return ConversationHandler.END
    prompt_text = ""
    if choice == 'recharge': prompt_text = "আপনার রিচার্জ নাম্বারটি দিন:"
    elif choice == 'rocket': prompt_text = "আপনার Rocket অ্যাকাউন্ট নাম্বারটি দিন:"
    elif choice == 'binance': prompt_text = "আপনার Binance Pay ID বা ইমেইল দিন:"
    await query.edit_message_text(text=prompt_text)
    return ENTER_DETAILS

async def enter_withdraw_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['withdraw_details'] = update.message.text
    method = context.user_data['withdraw_method']
    if method == 'binance': prompt_text = f"কত USD উইথড্র করতে চান? (সর্বনিম্ন ${MIN_WITHDRAW['binance']})"
    else: prompt_text = f"কত টাকা উইথড্র করতে চান? (সর্বনিম্ন ৳{MIN_WITHDRAW[method]})"
    await update.message.reply_text(prompt_text)
    return CONFIRM_WITHDRAW

async def confirm_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amount = float(update.message.text)
    except ValueError:
        await update.message.reply_text("অনুগ্রহ করে একটি সঠিক সংখ্যা লিখুন।"); return CONFIRM_WITHDRAW
    user_id = update.effective_user.id
    method = context.user_data['withdraw_method']
    details = context.user_data['withdraw_details']
    balance = get_user_balance(user_id)
    min_amount = MIN_WITHDRAW[method]
    amount_in_bdt = amount if method != 'binance' else amount * USD_TO_BDT_RATE
    if amount < min_amount:
        await update.message.reply_text(f"দুঃখিত, সর্বনিম্ন উইথড্র পরিমাণ হলো {min_amount} {'USD' if method == 'binance' else 'BDT'}"); return CONFIRM_WITHDRAW
    if balance < amount_in_bdt:
        await update.message.reply_text("দুঃখিত, আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই।"); return ConversationHandler.END
    update_user_balance(user_id, -amount_in_bdt)
    admin_message = (f"🔔 নতুন উইথড্র অনুরোধ!\n\n"
                     f"👤 ব্যবহারকারী: {update.effective_user.full_name} (ID: `{user_id}`)\n"
                     f"🏦 মাধ্যম: {method.capitalize()}\n"
                     f"📝 অ্যাকাউন্ট/নাম্বার: `{details}`\n"
                     f"💰 পরিমাণ: {amount:.2f} {'USD' if method == 'binance' else 'BDT'}")
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_message, parse_mode='Markdown')
    await update.message.reply_text("✅ আপনার উইথড্র অনুরোধ সফলভাবে পাঠানো হয়েছে।")
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("উইথড্র প্রক্রিয়া বাতিল করা হয়েছে।")
    return ConversationHandler.END

# --- অ্যাডমিন কমান্ড ---
async def broadcast_to_all_users(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    user_ids = get_all_user_ids()
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            await asyncio.sleep(BROADCAST_SLEEP_TIME)
        except Forbidden: print(f"ব্যর্থ: ব্যবহারকারী {user_id} বটকে ব্লক করেছেন।")
        except Exception as e: print(f"ব্রডকাস্ট করতে সমস্যা: {e}")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    if not context.args: await update.message.reply_text("ব্যবহার: /add <num1> <num2> ..."); return
    added_count = add_numbers_to_db(context.args)
    await update.message.reply_text(f"সফলভাবে {added_count} টি নতুন নাম্বার যোগ করা হয়েছে।")
    if added_count > 0:
        total_numbers = get_total_numbers_count()
        notification_message = f"🎉 সুসংবাদ! আমাদের স্টকে নতুন নাম্বার যোগ করা হয়েছে।\n\n현재 মোট নাম্বার সংখ্যা: {total_numbers}টি।"
        await broadcast_to_all_users(context, notification_message)

async def clearall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    deleted_count = clear_all_numbers_from_db()
    await update.message.reply_text(f"সফলভাবে {deleted_count} টি নাম্বার তালিকা থেকে মুছে ফেলা হয়েছে।")
    if deleted_count > 0:
        await broadcast_to_all_users(context, "দুঃখিত, আমাদের স্টকের সকল নাম্বার শেষ হয়ে গেছে। খুব শীঘ্রই আবার নাম্বার যোগ করা হবে।")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    count = get_total_numbers_count()
    await update.message.reply_text(f"এখনও {count} টি নাম্বার অবশিষ্ট আছে।")
    notification_message = f"📊 নাম্বার আপডেট!\n\n📦 আমাদের স্টকে বর্তমানে মোট {count} টি নাম্বার উপলব্ধ আছে।"
    await broadcast_to_all_users(context, notification_message)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    if not context.args or len(context.args) != 1: await update.message.reply_text("ব্যবহার: /delete <number>"); return
    if delete_number_from_db(context.args[0]): await update.message.reply_text(f"নাম্বার '{context.args[0]}' মুছে ফেলা হয়েছে।")
    else: await update.message.reply_text(f"নাম্বার '{context.args[0]}' পাওয়া যায়নি।")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    if not context.args: await update.message.reply_text("ব্যবহার: /broadcast <আপনার মেসেজ>"); return
    broadcast_message = " ".join(context.args); user_ids = get_all_user_ids()
    await update.message.reply_text(f"{len(user_ids)} জন ব্যবহারকারীকে মেসেজ পাঠানো শুরু হচ্ছে..."); success_count = 0; fail_count = 0
    for user_id in user_ids:
        try: await context.bot.send_message(chat_id=user_id, text=broadcast_message); success_count += 1
        except Forbidden: fail_count += 1
        except Exception as e: fail_count += 1
        await asyncio.sleep(BROADCAST_SLEEP_TIME)
    await update.message.reply_text(f"ব্রডকাস্ট সম্পন্ন!\nসফল: {success_count} জন। ব্যর্থ: {fail_count} জন।")

# === প্রধান ফাংশন ===
async def main():
    # ডাটাবেস এবং টেবিল তৈরি করা
    setup_database()
    
    # টেলিগ্রাম বট অ্যাপ্লিকেশন তৈরি
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # উইথড্র করার জন্য Conversation Handler
    withdraw_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{BTN_WITHDRAW}$'), handle_withdraw_message)],
        states={
            CHOOSE_METHOD: [CallbackQueryHandler(choose_withdraw_method)],
            ENTER_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_withdraw_details)],
            CONFIRM_WITHDRAW: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_withdraw)],
        },
        fallbacks=[CommandHandler('cancel', cancel_withdraw)],
        per_message=False
    )

    # সকল বট হ্যান্ডলার যোগ করা
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(MessageHandler(filters.Regex(f'^{BTN_GET_NUMBER}$'), handle_get_number_message))
    ptb_app.add_handler(MessageHandler(filters.Regex(f'^{BTN_ACCOUNT}$'), handle_account_message))
    ptb_app.add_handler(MessageHandler(filters.Regex(f'^{BTN_BALANCE}$'), handle_balance_message))
    ptb_app.add_handler(withdraw_handler)
    ptb_app.add_handler(CallbackQueryHandler(refresh_button_callback, pattern='^refresh_button$'))

    # অ্যাডমিন হ্যান্ডলার যোগ করা
    ptb_app.add_handler(CommandHandler("add", add_command))
    ptb_app.add_handler(CommandHandler("delete", delete_command))
    ptb_app.add_handler(CommandHandler("clearall", clearall_command))
    ptb_app.add_handler(CommandHandler("stats", stats_command))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))

    # PTB অ্যাপ্লিকেশন এবং Telethon ক্লায়েন্ট একসাথে চালানো
    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling()
        
        async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
            print("Telethon ইউজার ক্লায়েন্ট সফলভাবে চালু হয়েছে।")
            
            # Telethon-এর handler যোগ করা
            @client.on(events.NewMessage(chats=os.environ.get("SOURCE_CHANNEL"))) # Source Channel-ও Secret থেকে নেওয়া ভালো
            async def forwarder_handler(event):
                message_text = event.message.text
                if not message_text: return
                
                numbers_in_message = re.findall(r'(\+?\d[ \d\-\(\)]{7,}\d)', message_text)
                if not numbers_in_message: return

                for raw_number in numbers_in_message:
                    cleaned_number = clean_phone_number(raw_number)
                    if not (8 <= len(cleaned_number) <= 15): continue
                    
                    assigned_user = get_assigned_user(cleaned_number)
                    if assigned_user:
                        try:
                            # OTP মেসেজটি ব্যবহারকারীকে পাঠানো
                            otp_message = f"🔑 **OTP কোড এসেছে!**\n\n
