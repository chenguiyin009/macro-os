from pydantic import BaseModel, Field, model_validator
from typing import Dict


class TechSubSectorBudget(BaseModel):
    TECH_HW: float = Field(0.0, ge=0, le=1)
    TECH_HYPER: float = Field(0.0, ge=0, le=1)
    TECH_AI_APP: float = Field(0.0, ge=0, le=1)
    TECH_OPTICAL: float = Field(0.0, ge=0, le=1)
    TECH_POWER: float = Field(0.0, ge=0, le=1)
    TECH_MEMORY: float = Field(0.0, ge=0, le=1)
    TECH_NET: float = Field(0.0, ge=0, le=1)


class SectorBudget(BaseModel):
    tech_budget: float = Field(0.0, ge=0, le=1)
    defensive_budget: float = Field(0.0, ge=0, le=1)
    tech_subsectors: TechSubSectorBudget = Field(default_factory=TechSubSectorBudget)


class AssetClassBudget(BaseModel):
    equity_budget: float = Field(0.0, ge=0, le=1)
    fixed_income_budget: float = Field(0.0, ge=0, le=1)
    commodity_budget: float = Field(0.0, ge=0, le=1)
    cash_budget: float = Field(0.0, ge=0, le=1)


class AllocationProposal(BaseModel):
    macro_narrative: str = Field("", max_length=250)
    risk_budget: float = Field(..., ge=0, le=1)
    asset_classes: AssetClassBudget = Field(default_factory=AssetClassBudget)
    sectors: SectorBudget = Field(default_factory=SectorBudget)

    @model_validator(mode="after")
    def validate_budget_tree(self) -> "AllocationProposal":
        l2 = sum([self.asset_classes.equity_budget, self.asset_classes.fixed_income_budget,
                  self.asset_classes.commodity_budget, self.asset_classes.cash_budget])
        if not (0.99 <= l2 <= 1.01):
            raise ValueError("L2 budget sum %.2f != 1.0" % l2)

        if self.asset_classes.equity_budget > 0:
            l3 = self.sectors.tech_budget + self.sectors.defensive_budget
            if not (0.99 <= l3 <= 1.01):
                raise ValueError("L3 budget sum %.2f != 1.0" % l3)

        if self.sectors.tech_budget > 0:
            l4 = sum(self.sectors.tech_subsectors.model_dump().values())
            if not (0.99 <= l4 <= 1.01):
                raise ValueError("L4 budget sum %.2f != 1.0" % l4)
        return self

    def flatten_to_absolute_weights(self) -> Dict[str, float]:
        w = {"CASH": self.asset_classes.cash_budget, "GLD": self.asset_classes.commodity_budget,
             "THEME_DEF": self.asset_classes.equity_budget * self.sectors.defensive_budget}
        ta = self.asset_classes.equity_budget * self.sectors.tech_budget
        for asset, sw in self.sectors.tech_subsectors.model_dump().items():
            if sw > 0:
                w[asset] = ta * sw
        return {k: round(v, 4) for k, v in w.items()}
