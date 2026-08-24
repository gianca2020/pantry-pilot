from enum import StrEnum


class Category(StrEnum):
    PROTEIN = "protein"
    CARB = "carb"
    GREEN = "green"
    STAPLE = "staple"


class TrackingMode(StrEnum):
    QUANTITY = "quantity"
    PRESENCE = "presence"


class BaseUnit(StrEnum):
    EACH = "each"
    GRAM = "gram"
    MILLILITER = "milliliter"


class StockStatus(StrEnum):
    OUT = "out"
    LOW = "low"
    OK = "ok"


class TxnReason(StrEnum):
    INITIAL = "initial"
    RESTOCK = "restock"
    CONSUME = "consume"
    DISCARD = "discard"
    ADJUST = "adjust"
