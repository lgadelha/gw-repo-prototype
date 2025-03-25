import datetime
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import (
    TIMESTAMP, create_engine, Column, String, Numeric, ForeignKey, Interval
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from pydantic import BaseModel
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/dbname")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------------------------------
# SQLAlchemy Models
# ----------------------------------------

class WorkflowExecution(Base):
    __tablename__ = "workflow_execution"
    
    id = Column(String, primary_key=True)
#    start_time = Column(TIMESTAMP)
    start_time = Column(Numeric)
    duration = Column(Numeric)
    run_name = Column(String)
    nextflow_version = Column(String)
    final_state = Column(String)
    revision_id = Column(String)

    process_executions = relationship("ProcessExecution", back_populates="workflow_execution")


class ProcessExecution(Base):
    __tablename__ = "process_execution"

    id = Column(String, primary_key=True)
    workflow_execution_id = Column(String, ForeignKey("workflow_execution.id"))
    process_name = Column(String)
    module_name = Column(String)
    container_name = Column(String)
    final_status = Column(String)
    exit_code = Column(Numeric)
#    start_time = Column(TIMESTAMP)
    start_time = Column(Numeric)
    duration = Column(Numeric)
    cpus_requested = Column(Numeric)
    time_requested = Column(Numeric)
    storage_requested = Column(Numeric)
    memory_requested = Column(Numeric)
    realtime = Column(Numeric)
    queue_name = Column(String)
    percent_cpu = Column(Numeric)
    percent_memory = Column(Numeric)
    peak_rss = Column(Numeric)
    peak_vmem = Column(Numeric)
    read_char = Column(Numeric)
    write_char = Column(Numeric)

    workflow_execution = relationship("WorkflowExecution", back_populates="process_executions")
    parameters = relationship("ProcessExecutionParameterInput", back_populates="process_execution", cascade="all, delete-orphan")
    input_files = relationship("ProcessExecutionInputFile", back_populates="process_execution", cascade="all, delete-orphan")
    output_files = relationship("ProcessExecutionOutputFile", back_populates="process_execution", cascade="all, delete-orphan")


class ProcessExecutionParameterInput(Base):
    __tablename__ = "process_execution_parameter_input"

    process_execution_id = Column(String, ForeignKey("process_execution.id"), primary_key=True)
    parameter_name = Column(String, primary_key=True)
    parameter_value = Column(String)

    process_execution = relationship("ProcessExecution", back_populates="parameters")


class ProcessExecutionInputFile(Base):
    __tablename__ = "process_execution_input_file"

    process_execution_id = Column(String, ForeignKey("process_execution.id"), primary_key=True)
    filename = Column(String, primary_key=True)
    md5hash = Column(String, primary_key=True)

    process_execution = relationship("ProcessExecution", back_populates="input_files")


class ProcessExecutionOutputFile(Base):
    __tablename__ = "process_execution_output_file"

    process_execution_id = Column(String, ForeignKey("process_execution.id"), primary_key=True)
    filename = Column(String, primary_key=True)
    md5hash = Column(String, primary_key=True)

    process_execution = relationship("ProcessExecution", back_populates="output_files")


Base.metadata.create_all(bind=engine)

# ----------------------------------------
# FastAPI App & Dependency
# ----------------------------------------

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------------------------------
# Pydantic Models
# ----------------------------------------

class WorkflowExecutionBase(BaseModel):
#    start_time: datetime.datetime | None = None
    start_time: float | None = None
    duration: float | None = None
    final_state: str | None = None
    run_name: str | None = None
    nextflow_version: str | None = None
    revision_id: str | None = None

class WorkflowExecutionCreate(WorkflowExecutionBase):
    id: str

class WorkflowExecutionResponse(WorkflowExecutionBase):
    id: str

    class Config:
        from_attributes = True


class ProcessExecutionBase(BaseModel):
    workflow_execution_id: str
    process_name: str
    module_name: str
    container_name: str | None = None
    final_status: str
    exit_code: int | None = None
#    start_time: datetime.datetime | None = None
    start_time: float | None = None
    duration: float | None = None
    cpus_requested: float | None = None
    time_requested: float | None = None
    storage_requested: float | None = None
    memory_requested: float | None = None
    realtime: float | None = None
    queue_name: str | None = None
    percent_cpu: float | None = None
    percent_memory: float | None = None
    peak_rss: float | None = None
    peak_vmem: float | None = None
    read_char: float | None = None
    write_char: float | None = None

class ProcessExecutionCreate(ProcessExecutionBase):
    id: str

class ProcessExecutionResponse(ProcessExecutionBase):
    id: str

    class Config:
        from_attributes = True


# CRUD Operations 

@app.post("/executions/", response_model=WorkflowExecutionResponse)
def create_execution(execution: WorkflowExecutionCreate, db: Session = Depends(get_db)):
    db_execution = WorkflowExecution(**execution.model_dump())
    db.add(db_execution)
    db.commit()
    db.refresh(db_execution)
    return db_execution

@app.get("/executions/{execution_id}", response_model=WorkflowExecutionResponse)
def read_execution(execution_id: str, db: Session = Depends(get_db)):
    execution = db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution

@app.post("/processes/", response_model=ProcessExecutionResponse)
def create_process_execution(process: ProcessExecutionCreate, db: Session = Depends(get_db)):
    db_process = ProcessExecution(**process.model_dump())
    db.add(db_process)
    db.commit()
    db.refresh(db_process)
    return db_process

@app.get("/processes/{process_id}", response_model=ProcessExecutionResponse)
def read_process_execution(process_id: str, db: Session = Depends(get_db)):
    process = db.query(ProcessExecution).filter(ProcessExecution.id == process_id).first()
    if process is None:
        raise HTTPException(status_code=404, detail="Process execution not found")
    return process

app.delete("/processes/{process_id}")
def delete_process_execution(process_id: str, db: Session = Depends(get_db)):
    process = db.query(ProcessExecution).filter(ProcessExecution.id == process_id).first()
    if process is None:
        raise HTTPException(status_code=404, detail="Process execution not found")
    
    db.delete(process)
    db.commit()
    return {"message": "Process execution deleted successfully"}
