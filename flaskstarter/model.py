from peewee import (
    CharField,
    BooleanField,
    TextField,
    DecimalField,
    IntegerField,
    Model,
    Check,
    ForeignKeyField,
)

from flaskstarter.services.external.chargingapi import charge

from .extensions import db
from .utils.taxes import calculate_tax, calculate_shipping_price


class Product(Model):
    name = CharField()
    in_stock = BooleanField(default=True)
    description = TextField(null=True)
    price = DecimalField(
        max_digits=5, decimal_places=2, constraints=[Check("price > 0")]
    )
    weight = IntegerField(null=True, constraints=[Check("weight > 0")])
    image = CharField()

    class Meta:
        database = db


class ShippingInformation(Model):
    country = CharField()
    address = CharField()
    postal_code = CharField()
    city = CharField()
    province = CharField(max_length=2)

    class Meta:
        database = db


class ProductOrderQuantity(Model):
    # TODO: uncomment line below and move class definition to have multiple products per order (next version)
    # oid = ForeignKeyField(Order, backref='products')
    pid = ForeignKeyField(Product, backref="order_quantities")
    quantity = IntegerField(constraints=[Check("quantity > 0")])

    class Meta:
        database = db


class CreditCardDetails(Model):
    name = CharField()
    number = DecimalField(max_digits=16, decimal_places=0)
    expiration_year = DecimalField(max_digits=4, decimal_places=0)
    cvv = DecimalField(max_digits=3, decimal_places=0)
    expiration_month = DecimalField(
        max_digits=2,
        decimal_places=0,
        constraints=[Check("expiration_month < 13"), Check("expiration_month > 0")],
    )

    class Meta:
        database = db


class Transaction(Model):
    id = CharField(max_length=32, primary_key=True)
    success = BooleanField()
    amount_charged = DecimalField(decimal_places=2)

    class Meta:
        database = db


class Order(Model):
    product = ForeignKeyField(ProductOrderQuantity)
    email = CharField(null=True)
    credit_card = ForeignKeyField(CreditCardDetails, null=True)
    shipping_information = ForeignKeyField(
        ShippingInformation, backref="orders_shipped_there", null=True
    )
    transaction = ForeignKeyField(Transaction, null=True)
    paid = BooleanField(default=False)

    class Meta:
        database = db


def serialize_product(product: Product) -> dict:
    return {
        "name": product.name,
        "id": product.get_id(),
        "in_stock": bool(product.in_stock),
        "description": product.description,
        "price": product.price,
        "weight": product.weight,
        "image": product.image,
    }


def get_products() -> list[dict]:
    return [p for p in map(serialize_product, Product.select())]


def add_product(
    name: str,
    in_stock: bool,
    description: str | None,
    price: float,
    weight: int,
    image: str,
    id: int | None = None,
):
    Product(
        id=id,
        name=name,
        in_stock=in_stock,
        description=description,
        price=price,
        weight=weight,
        image=image,
    ).save(force_insert=True)


def drop_products():
    Product.delete().execute()


def add_order(product_id: int, quantity: int) -> int:
    poq = ProductOrderQuantity(pid=Product.get(id=product_id), quantity=quantity)
    poq.save()
    order = Order(product=poq)
    order.save()
    return order.get_id()


class OrderNotFound(Exception):
    pass


def get_order(order_id: int) -> dict:
    order: Order | None = Order.get_or_none(order_id)
    if order is None:
        raise OrderNotFound()

    total_price = float(order.product.pid.price * order.product.quantity)
    credit_card: CreditCardDetails | None = order.credit_card
    shipping_information: ShippingInformation | None = order.shipping_information
    transaction: Transaction | None = order.transaction

    return {
        "order": {
            "id": order_id,
            "total_price": total_price,
            "total_price_tax": (
                None
                if order.shipping_information is None
                else calculate_tax(order.shipping_information.province) * total_price
                + total_price
            ),
            "email": order.email,
            "credit_card": (
                {}
                if credit_card is None
                else {
                    "name": credit_card.name,
                    "first_digits": str(credit_card.number)[:4],
                    "last_digits": str(credit_card.number)[-4:],
                    "expiration_year": int(credit_card.expiration_year),
                    "expiration_month": int(credit_card.expiration_month),
                }
            ),
            "shipping_information": (
                {}
                if shipping_information is None
                else {
                    "country": shipping_information.country,
                    "address": shipping_information.address,
                    "postal_code": shipping_information.postal_code,
                    "city": shipping_information.city,
                    "province": shipping_information.province,
                }
            ),
            "transaction": (
                {}
                if transaction is None
                else {
                    "id": transaction.id,
                    "success": transaction.success,
                    "amount_charged": transaction.amount_charged,
                }
            ),
            "paid": order.paid,
            "product": {
                "id": order.product.pid.get_id(),
                "quantity": order.product.quantity,
            },
            "shipping_price": calculate_shipping_price(
                order.product.pid.weight * order.product.quantity
            ),
        }
    }


def put_order_shipping_information(
    order_id: int,
    email: str,
    country: str,
    address: str,
    postal_code: str,
    city: str,
    province: str,
) -> dict:
    order: Order | None = Order.get_or_none(order_id)
    if order is None:
        raise OrderNotFound()

    si = order.shipping_information
    if si is None:  # no shipping information yet, create it
        si = ShippingInformation(
            country=country,
            address=address,
            postal_code=postal_code,
            city=city,
            province=province,
        )
        si.save()
        order.shipping_information = si
        order.email = email
        order.save()
    else:  # shipping information exists, update it
        si.country = country
        si.address = address
        si.postal_code = postal_code
        si.city = city
        si.province = province
        si.save()
        order.email = email
        order.save()

    return get_order(order_id)


def put_order_credit_card(
    order_id: int,
    name: str,
    number: int,
    expiration_year: int,
    cvv: int,
    expiration_month: int,
) -> dict:
    order: Order | None = Order.get_or_none(order_id)
    if order is None:
        raise OrderNotFound()

    credit_card: None | CreditCardDetails = order.credit_card
    with db.manual_commit() as _:
        db.begin()
        if credit_card is None:  # card does not exist yet
            credit_card = CreditCardDetails(
                name=name,
                number=number,
                expiration_year=expiration_year,
                cvv=cvv,
                expiration_month=expiration_month,
            )
            credit_card.save()
            order.credit_card = credit_card
            order.save()
        else:  # card exists, update it
            credit_card.name = name
            credit_card.number = number
            credit_card.expiration_year = expiration_year
            credit_card.cvv = cvv
            credit_card.expiration_month = expiration_month
            credit_card.save()
        db.commit()

    order_dict = get_order(order_id)
    transaction_dict = charge(
        name,
        number,
        expiration_year,
        cvv,
        expiration_month,
        order_dict["order"]["total_price_tax"] + order_dict["order"]["shipping_price"],
    )["transaction"]
    with db.manual_commit() as _:
        db.begin()
        transaction = Transaction(
            id=transaction_dict["id"],
            success=transaction_dict["success"],
            amount_charged=transaction_dict["amount_charged"],
        )
        transaction.save(force_insert=True)
        order.transaction = transaction
        order.save()
        db.commit()

    return get_order(order_id)
