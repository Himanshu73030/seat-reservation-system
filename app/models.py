from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    seats: list[str] = Field(min_length=1, max_length=100_000)
    price_paise: int = Field(strict=True, ge=0, le=9_000_000_000_000_000)

    @field_validator("seats")
    @classmethod
    def validate_seats(cls, seats: list[str]) -> list[str]:
        if any(not seat or len(seat) > 32 for seat in seats):
            raise ValueError("seat IDs must contain 1 to 32 characters")
        if len(set(seats)) != len(seats):
            raise ValueError("seat IDs must be unique")
        return seats


class ReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seats: list[str] = Field(min_length=1, max_length=100)

    @field_validator("seats")
    @classmethod
    def validate_seats(cls, seats: list[str]) -> list[str]:
        if any(not seat or len(seat) > 32 for seat in seats):
            raise ValueError("seat IDs must contain 1 to 32 characters")
        if len(set(seats)) != len(seats):
            raise ValueError("seat IDs must be unique")
        return seats