from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTAuthorizationCredentials
from sqlmodel import SQLModel, Field, create_engine, Session, Relationship, select
from typing import Optional, List, Annotated
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@db/dbname")
engine = create_engine(DATABASE_URL, echo=True)

# API Key Authentication
API_KEY = os.getenv("API_KEY", "enter_default_api_key_here")
if not API_KEY:
    raise EnvironmentError("Missing API_KEY environment variable")
security = HTTPBearer()

def verify_api_key(credentials: HTTAuthorizationCredentials = Security(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=401, # Unauthorized
            detail="Invalid API Key"
        )
    return credentials.credentials

# SQLModel Models
class ProcessExecutionParameterInput(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    parameter_name: str = Field(primary_key=True)
    parameter_value: str

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="parameters")

class ProcessExecutionInputFile(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    filename: str = Field(primary_key=True)
    xxhash128: str = Field(primary_key=True)

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="input_files")

class ProcessExecutionOutputFile(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    filename: str = Field(primary_key=True)
    xxhash128: str = Field(primary_key=True)

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="output_files")


class WorkflowExecution(SQLModel, table=True):
    id: str = Field(primary_key=True)
    start_time: Optional[float] = None
    duration: Optional[float] = None
    run_name: Optional[str] = None
    nextflow_version: Optional[str] = None
    final_state: Optional[str] = None
    revision_id: Optional[str] = None

    process_executions: List["ProcessExecution"] = Relationship(back_populates="workflow_execution")


class ProcessExecution(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workflow_execution_id: str = Field(foreign_key="workflowexecution.id")
    process_name: str
    module_name: Optional[str] = None
    container_name: Optional[str] = None
    final_status: str
    exit_code: int
    start_time: float
    duration: float
    cpus_requested: Optional[float] = None
    time_requested: Optional[float] = None
    storage_requested: Optional[float] = None
    memory_requested: Optional[float] = None
    realtime: float
    queue_name: Optional[str] = None
    percent_cpu: float
    percent_memory: float
    peak_rss: float
    peak_vmem: float
    read_char: float
    write_char: float

    workflow_execution: Optional[WorkflowExecution] = Relationship(back_populates="process_executions")
    parameters: List[ProcessExecutionParameterInput] = Relationship(back_populates="process_execution")
    input_files: List[ProcessExecutionInputFile] = Relationship(back_populates="process_execution")
    output_files: List[ProcessExecutionOutputFile] = Relationship(back_populates="process_execution")


# App and DB dependency
app = FastAPI()

def get_session():
    with Session(engine) as session:
        yield session

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

# Routes

@app.post("/workflows/", response_model=WorkflowExecution)
def create_workflow(
    execution: WorkflowExecution, 
    session: Session = Depends(get_session), 
    api_key: str = Depends(verify_api_key)
):
    session.add(execution)
    session.commit()
    session.refresh(execution)
    return execution

@app.get("/workflows/{execution_id}", response_model=WorkflowExecution)
def get_workflow(execution_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    workflow = session.get(WorkflowExecution, execution_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow

@app.post("/processes/", response_model=ProcessExecution)
def create_process(process: ProcessExecution, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.add(process)
    session.commit()
    session.refresh(process)
    return process

@app.get("/processes/{process_id}", response_model=ProcessExecution)
def get_process(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    process = session.get(ProcessExecution, process_id)
    if not process:
        raise HTTPException(status_code=404, detail="Process not found")
    return process

@app.delete("/processes/{process_id}")
def delete_process(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    process = session.get(ProcessExecution, process_id)
    if not process:
        raise HTTPException(status_code=404, detail="Process not found")
    session.delete(process)
    session.commit()
    return {"message": "Process deleted successfully"}

@app.post("/parameters/", response_model=ProcessExecutionParameterInput)
def create_parameter(param: ProcessExecutionParameterInput, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.add(param)
    session.commit()
    session.refresh(param)
    return param

@app.get("/parameters/{process_id}", response_model=List[ProcessExecutionParameterInput])
def get_parameters(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    result = session.exec(select(ProcessExecutionParameterInput).where(ProcessExecutionParameterInput.process_execution_id == process_id)).all()
    return result

@app.delete("/parameters/{process_id}")
def delete_parameters(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(
        select(ProcessExecutionParameterInput)
        .where(ProcessExecutionParameterInput.process_execution_id == process_id)
    ).delete()
    session.commit()
    return {"message": "Parameters deleted successfully"}

@app.post("/input_files/", response_model=ProcessExecutionInputFile)
def create_input_file(file: ProcessExecutionInputFile, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.add(file)
    session.commit()
    session.refresh(file)
    return file

@app.get("/input_files/{process_id}", response_model=List[ProcessExecutionInputFile])
def get_input_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(ProcessExecutionInputFile).where(ProcessExecutionInputFile.process_execution_id == process_id)).all()

@app.delete("/input_files/{process_id}")
def delete_input_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(select(ProcessExecutionInputFile).where(ProcessExecutionInputFile.process_execution_id == process_id)).delete()
    session.commit()
    return {"message": "Input files deleted successfully"}

@app.post("/output_files/", response_model=ProcessExecutionOutputFile)
def create_output_file(file: ProcessExecutionOutputFile, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.add(file)
    session.commit()
    session.refresh(file)
    return file

@app.get("/output_files/{process_id}", response_model=List[ProcessExecutionOutputFile])
def get_output_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(ProcessExecutionOutputFile).where(ProcessExecutionOutputFile.process_execution_id == process_id)).all()

@app.delete("/output_files/{process_id}")
def delete_output_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(select(ProcessExecutionOutputFile).where(ProcessExecutionOutputFile.process_execution_id == process_id)).delete()
    session.commit()
    return {"message": "Output files deleted successfully"}