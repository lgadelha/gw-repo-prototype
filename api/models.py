from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime, timezone


class Institut(SQLModel, table=True):
    """Institute/organization tracking for multi-tenant resource monitoring."""
    id: str = Field(primary_key=True)  # e.g., "DKFZ", "EMBL", "UNKNOWN"
    name: str
    cost_per_cpu_hour: Optional[float] = 0.0
    cost_per_gb_memory_hour: Optional[float] = 0.0
    storage_cost_per_gb_month: Optional[float] = 0.0
    cpu_quota: Optional[float] = None
    memory_quota_gb: Optional[float] = None
    storage_quota_gb: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Relationships
    workflows: List["WorkflowExecution"] = Relationship(back_populates="institut")
    process_executions: List["ProcessExecution"] = Relationship(back_populates="institut")


class Hardwareinventory(SQLModel, table=True):
    """Hardware inventory per institute for CPU model tracking."""
    id: Optional[int] = Field(default=None, primary_key=True)
    institute_id: str = Field(foreign_key="institut.id")
    cpu_model: str
    cpu_tdp_watts: Optional[float] = None
    memory_type: Optional[str] = None
    gpu_model: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class Co2footprint(SQLModel, table=True):
    """Per-process CO2 footprint and energy consumption data."""
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    energy_consumption_mwh: float
    co2e_mg: float
    co2e_market_mg: Optional[float] = None
    carbon_intensity_gco2e_kwh: float
    powerdraw_cpu_w: float
    cpu_model: str
    raw_energy_processor_mwh: float
    raw_energy_memory_mwh: float


class Workflowco2summary(SQLModel, table=True):
    """Workflow-level CO2 footprint summary."""
    workflow_execution_id: str = Field(foreign_key="workflowexecution.id", primary_key=True)
    total_energy_mwh: float
    total_co2e_mg: float
    car_km_equivalent: float
    tree_sequestration_time_sec: float


class Samplemetric(SQLModel, table=True):
    """Per-sample domain metric (e.g. MultiQC general stats), key-value per sample.
    `sample` matches ProcessExecution.tag for nf-core pipelines (the meta.id)."""
    workflow_execution_id: str = Field(foreign_key="workflowexecution.id", primary_key=True)
    sample: str = Field(primary_key=True)
    key: str = Field(primary_key=True)
    value_num: Optional[float] = None
    value_text: Optional[str] = None


class Optimizationrule(SQLModel, table=True):
    """ML-based or manual optimization rules per institute."""
    id: Optional[int] = Field(default=None, primary_key=True)
    institute_id: str = Field(foreign_key="institut.id")
    process_name_pattern: str  # regex pattern to match process names
    recommended_cpus: Optional[int] = None
    recommended_memory_mb: Optional[int] = None
    recommended_time_sec: Optional[int] = None
    ml_model_version: Optional[str] = None
    confidence_score: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Mlmodelmetadata(SQLModel, table=True):
    """Metadata for trained ML models - per-process models."""
    id: Optional[int] = Field(default=None, primary_key=True)
    process_name: str  # "BCFTOOLS_FILTER" (nf-core normalized, uppercase)
    resource_type: str  # "memory", "time", or "cpu"
    model_type: str  # "gradient_boosting"
    training_samples: int  # Process-specific sample count
    accuracy_metrics: str  # JSON string with RMSE, MAE, R² etc.
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_artifact_path: str  # "/code/models/BCFTOOLS_FILTER_memory.pkl"
    is_fallback_model: bool = False  # True for global fallback model
