import json
import logging
import os
from datetime import datetime

import openai  # Importar la librería de OpenAI
from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String, Numeric, ForeignKey, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

# Load environment variables from .env file
load_dotenv()

# Configurar API de OpenAI
openai.api_key = os.getenv(
    "OPENAI_API_KEY")

# Load responses from JSON file
with open("text/responses.json", "r", encoding="utf-8") as f:
    responses = json.load(f)

# Load rules from JSON file
with open("text/rulesGPT.json", "r", encoding="utf-8") as f:
    rules = json.load(f)["rules"]

# Construct system context dynamically
system_context = {
    "role": "system",
    "content": " ".join(rules)
}

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# Function to get the greeting based on the current time
def get_greeting() -> str:
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12:
        return "Buenos días"
    elif 12 <= current_hour < 18:
        return "Buenas tardes"
    else:
        return "Buenas noches"


# Database model for Category
class Category(Base):
    __tablename__ = 'Category'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    slug = Column(String, index=True)


# Database model for Product
class Product(Base):
    __tablename__ = 'Product'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    price = Column(Numeric(10, 2), index=True)  # Changed String to Numeric
    image = Column(String)
    categoryId = Column(Integer, ForeignKey('Category.id'))

    # Define the relationship with OrderProducts
    orders = relationship("OrderProducts", back_populates="product")


# Database model for Order
class Order(Base):
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True)
    order_products = relationship("OrderProducts", back_populates="order")


# Database model for OrderProducts
class OrderProducts(Base):
    __tablename__ = 'OrderProducts'
    id = Column(Integer, primary_key=True)
    orderId = Column(Integer, ForeignKey('orders.id'))
    productId = Column(Integer, ForeignKey('Product.id'))
    quantity = Column(Integer)

    # Define relationships
    order = relationship("Order", back_populates="order_products")
    product = relationship("Product", back_populates="orders")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a greeting message followed by inline buttons."""
    logger.info("Handling /start command")

    # Determine the source of the update
    if isinstance(update, Update) and update.message:
        user_first_name = update.message.from_user.first_name
        chat_id = update.message.chat_id
    elif isinstance(update, Update) and update.callback_query:
        user_first_name = update.callback_query.from_user.first_name
        chat_id = update.callback_query.message.chat_id
    else:
        logger.warning("Update does not have message or callback_query")
        return  # Exit if neither condition is met

    bot_name = "BotMesero"
    greeting = get_greeting()

    # Log the chat_id to ensure it's being captured correctly
    logger.info(f"Chat ID: {chat_id}")

    # Format the greeting message using Markdown
    greeting_message = responses["greeting_message"].format(
        user_first_name=user_first_name,
        chat_id=f"`{chat_id}`"  # Markdown format for code block
    )

    if isinstance(update, Update) and update.message:
        await update.message.reply_text(greeting_message, parse_mode='Markdown')
    elif isinstance(update, Update) and update.callback_query:
        await update.callback_query.message.edit_text(greeting_message, parse_mode='Markdown')

    keyboard = [
        [InlineKeyboardButton("Cuál es el menú de hoy 📋", callback_data="menu")],
        [InlineKeyboardButton("Cómo puedo realizar un pedido 📑❓", callback_data="pedido")],
        [InlineKeyboardButton("Preguntas acerca del Bot 🤖⁉", callback_data="otros")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if isinstance(update, Update) and update.message:
        await update.message.reply_text(responses["menu_message"], reply_markup=reply_markup)
    elif isinstance(update, Update) and update.callback_query:
        await update.callback_query.message.edit_text(responses["menu_message"], reply_markup=reply_markup)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages from users."""
    user_message = update.message.text
    logger.info(f"Received message from user: {user_message}")

    # Verificar si el mensaje del usuario pide el menú
    if "menú" in user_message.lower():
        fake_query = type('FakeQuery', (object,), {'edit_message_text': update.message.reply_text})
        await show_categories(fake_query)
        return

    # Verificar si el mensaje del usuario pregunta por el producto más pedido
    if any(keyword in user_message.lower() for keyword in
           ["producto más pedido", "orden más pedida", "producto más vendido"]):
        fake_query = type('FakeQuery', (object,), {'edit_message_text': update.message.reply_text})
        await show_most_ordered_product(fake_query)
        return

    # Obtener el historial de la conversación
    chat_id = update.message.chat_id
    if "conversation_history" not in context.chat_data:
        context.chat_data["conversation_history"] = []

    # Añadir el mensaje del usuario al historial
    context.chat_data["conversation_history"].append({"role": "user", "content": user_message})

    # Construir el historial de mensajes para el modelo
    messages = [system_context] + context.chat_data["conversation_history"]

    try:
        # Enviar el historial de mensajes al modelo GPT-4 para obtener una respuesta
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",  # Puedes usar "gpt-4" si tienes acceso a ese modelo
            messages=messages
        )

        # Extraer el contenido de la respuesta de GPT-4
        gpt_response = response.choices[0].message['content'].strip()

        # Añadir la respuesta del asistente al historial
        context.chat_data["conversation_history"].append({"role": "assistant", "content": gpt_response})

        # Enviar la respuesta de vuelta al usuario
        await update.message.reply_text(gpt_response)
    except Exception as e:
        logger.error(f"Error generating response: {e}")
        await update.message.reply_text("Lo siento, algo salió mal al procesar tu solicitud.")


def get_otros_keyboard() -> InlineKeyboardMarkup:
    """Returns the keyboard for 'Preguntas acerca del Bot'."""
    keyboard = [
        [InlineKeyboardButton("¿Cuánto tiempo demora en llegar mi pedido? ⏳", callback_data="tiempo_pedido")],
        [InlineKeyboardButton("¿Cuál es el producto más pedido de este establecimiento? 📊",
                              callback_data="producto_mas_pedido")],
        [InlineKeyboardButton("Puse mal una orden ¿Qué puedo hacer? 😬❓", callback_data="orden_mal")],
        [InlineKeyboardButton("El aplicativo no abre. 😖", callback_data="app_no_abre")],
        [InlineKeyboardButton("Sobre la información Proporcionada 🤔:", callback_data="info_proporcionada")],
        [InlineKeyboardButton("Regresar al Inicio ↩", callback_data="return_start")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query

    # CallbackQueries need to be answered, even if no notification to the user is needed
    await query.answer()

    logger.info(f"Callback data received: {query.data}")

    if query.data == "menu":
        await show_categories(query)
    elif query.data.startswith("category_"):
        category_id = int(query.data.split("_")[1])
        await show_products(query, category_id)
    elif query.data == "pedido":
        response = responses["pedido_response"]
        keyboard = [[InlineKeyboardButton("Regresar al Inicio ↩", callback_data="return_start")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=response, reply_markup=reply_markup)
    elif query.data == "otros":
        reply_markup = get_otros_keyboard()
        await query.edit_message_text(text=responses["other_questions_message"], reply_markup=reply_markup)
    elif query.data == "tiempo_pedido":
        response = responses["tiempo_pedido_response"]
        keyboard = [[InlineKeyboardButton("Regresar a las Preguntas ↩", callback_data="return_otros")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=response, reply_markup=reply_markup)
    elif query.data == "producto_mas_pedido":
        await show_most_ordered_product(query)
    elif query.data == "orden_mal":
        response = responses["orden_mal_response"]
        keyboard = [[InlineKeyboardButton("Regresar a las Preguntas ↩", callback_data="return_otros")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=response, reply_markup=reply_markup)
    elif query.data == "app_no_abre":
        response = responses["app_no_abre_response"]
        keyboard = [[InlineKeyboardButton("Regresar a las Preguntas ↩", callback_data="return_otros")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=response, reply_markup=reply_markup)
    elif query.data == "info_proporcionada":
        response = responses["info_proporcionada_response"]
        keyboard = [[InlineKeyboardButton("Regresar a las Preguntas ↩", callback_data="return_otros")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=response, reply_markup=reply_markup)
    elif query.data == "return_start":
        await start(update, context)
    elif query.data == "return_otros":
        reply_markup = get_otros_keyboard()
        await query.edit_message_text(text=responses["other_questions_message"], reply_markup=reply_markup)
    elif query.data == "return_categories":
        logger.info("Returning to categories")
        await show_categories(query)


async def show_categories(query: Update.callback_query):
    """Fetches categories from the database and shows them as inline buttons."""
    logger.info("Fetching categories from the database")
    async with SessionLocal() as session:
        async with session.begin():
            categories = (await session.execute(select(Category))).scalars().all()
            logger.info(f"Found categories: {categories}")

    if not categories:
        await query.edit_message_text(text="No hay categorías disponibles.")
        return

    keyboard = []
    for category in categories:
        keyboard.append([InlineKeyboardButton(category.name, callback_data=f"category_{category.id}")])

    keyboard.append([InlineKeyboardButton("Regresar al Inicio ↩", callback_data="return_start")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Selecciona una categoría:", reply_markup=reply_markup)


async def show_products(query: Update.callback_query, category_id: int):
    """Fetches products for a specific category and shows them as inline buttons."""
    logger.info(f"Fetching products for category {category_id}")
    async with SessionLocal() as session:
        async with session.begin():
            products = (await session.execute(
                select(Product).filter(Product.categoryId == category_id)
            )).scalars().all()
            logger.info(f"Found products: {products}")

    if not products:
        await query.edit_message_text(text="No hay productos disponibles en esta categoría.")
        return

    keyboard = []
    for product in products:
        price = f"{product.price:.2f}"  # Format price to 2 decimal places
        keyboard.append([InlineKeyboardButton(f"{product.name} - ${price}", callback_data=f"product_{product.id}")])

    keyboard.append([InlineKeyboardButton("Regresar a Categorías ↩", callback_data="return_categories")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Selecciona un producto:", reply_markup=reply_markup)


async def show_most_ordered_product(query: Update.callback_query) -> None:
    """Fetches and shows the most ordered product."""
    logger.info("Fetching the most ordered product")
    async with SessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                select(Product)
                .join(OrderProducts)
                .group_by(Product.id)
                .order_by(func.count(OrderProducts.id).desc())
                .limit(1)
            )
            most_ordered_product = result.scalars().first()
            logger.info(f"Most ordered product: {most_ordered_product}")

    if most_ordered_product:
        price = f"{most_ordered_product.price:.2f}"  # Format price to 2 decimal places
        response = f"El producto más pedido es {most_ordered_product.name} a un precio de ${price}."
    else:
        response = "No se encontró información sobre el producto más pedido."

    keyboard = [[InlineKeyboardButton("Regresar a las Preguntas ↩", callback_data="return_otros")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=response, reply_markup=reply_markup)


def main() -> None:
    """Start the bot."""
    logger.info("Starting the bot")
    application = Application.builder().token(os.getenv("BOT_TOKEN_2")).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # Manejar mensajes de texto

    application.run_polling()


if __name__ == "__main__":
    main()
