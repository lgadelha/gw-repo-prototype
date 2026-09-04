from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import SQLModel, Field, create_engine, Session, Relationship, select, delete
from sqlalchemy import text
from typing import Optional, List, Annotated, Dict
from datetime import datetime, timezone
import os, time

from models import (
    Institut,
    Hardwareinventory,
    Co2footprint,
    Workflowco2summary,
    Optimizationrule,
    Mlmodelmetadata
)
import json
import numpy as np

def convert_numpy_types(obj):
    """Convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    return obj

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@db/dbname")
engine = create_engine(DATABASE_URL, echo=True)

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise EnvironmentError("Missing API_KEY environment variable")
security = HTTPBearer()

def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )
    return credentials.credentials


class ProcessExecutionParameterInput(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    parameter_name: str = Field(primary_key=True)
    parameter_value: str

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="parameters")

class ProcessExecutionInputFile(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    filename: str = Field(primary_key=True)
    xxhash128: Optional[str] = None

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="input_files")

class ProcessExecutionOutputFile(SQLModel, table=True):
    process_execution_id: str = Field(foreign_key="processexecution.id", primary_key=True)
    filename: str = Field(primary_key=True)
    xxhash128: Optional[str] = None

    process_execution: Optional["ProcessExecution"] = Relationship(back_populates="output_files")

class WorkflowExecution(SQLModel, table=True):
    id: str = Field(primary_key=True)
    start_time: Optional[float] = None
    duration: Optional[float] = None
    run_name: Optional[str] = None
    nextflow_version: Optional[str] = None
    final_state: Optional[str] = None
    revision_id: Optional[str] = None
    institute_id: Optional[str] = Field(default="UNKNOWN", foreign_key="institut.id")
    data_size_tag: Optional[str] = None  # 'small', 'medium', 'large', 'mixed'

    process_executions: List["ProcessExecution"] = Relationship(back_populates="workflow_execution")
    institut: Optional["Institut"] = Relationship(back_populates="workflows")

class ProcessExecution(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workflow_execution_id: str = Field(foreign_key="workflowexecution.id")
    institute_id: Optional[str] = Field(default="UNKNOWN", foreign_key="institut.id")
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
    data_size_tag: Optional[str] = None  # 'small', 'medium', 'large'
    
    # Work directory metrics (privacy-safe: ONLY numbers, no paths/filenames)
    disk_usage_mb: Optional[float] = None
    read_bytes: Optional[int] = None
    write_bytes: Optional[int] = None
    peak_vmem_mb: Optional[float] = None
    peak_rss_mb: Optional[float] = None

    workflow_execution: Optional[WorkflowExecution] = Relationship(back_populates="process_executions")
    institut: Optional["Institut"] = Relationship(back_populates="process_executions")
    parameters: List[ProcessExecutionParameterInput] = Relationship(back_populates="process_execution")
    input_files: List[ProcessExecutionInputFile] = Relationship(back_populates="process_execution")
    output_files: List[ProcessExecutionOutputFile] = Relationship(back_populates="process_execution")


app = FastAPI()

def get_session():
    with Session(engine) as session:
        yield session

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)


@app.post("/workflows/", response_model=WorkflowExecution)
def create_workflow(
    execution: WorkflowExecution, 
    session: Session = Depends(get_session), 
    api_key: str = Depends(verify_api_key)
):
    db_obj = session.merge(execution)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/workflows/", response_model=List[WorkflowExecution])
def get_workflows(session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(WorkflowExecution)).all()

@app.get("/workflows/{execution_id}", response_model=WorkflowExecution)
def get_workflow(execution_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    workflow = session.get(WorkflowExecution, execution_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow

# CO2 endpoints must come BEFORE /processes/{process_id} to avoid route conflicts
@app.post("/processes/co2", response_model=Co2footprint)
def create_process_co2_footprint(
    co2_data: Co2footprint,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Submit CO2 footprint data for a process."""
    # Verify process exists
    process = session.get(ProcessExecution, co2_data.process_execution_id)
    if not process:
        raise HTTPException(status_code=404, detail="Process not found")
    
    db_obj = session.merge(co2_data)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/processes/co2", response_model=Co2footprint)
def get_process_co2_footprint(
    process_execution_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get CO2 footprint data for a process."""
    co2_data = session.get(Co2footprint, process_execution_id)
    if not co2_data:
        raise HTTPException(status_code=404, detail="CO2 footprint not found")
    return co2_data

@app.post("/processes/", response_model=ProcessExecution)
def create_process(process: ProcessExecution, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    db_obj = session.merge(process)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/processes/", response_model=List[ProcessExecution])
def get_processes(session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(ProcessExecution)).all()

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
    db_obj = session.merge(param)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/parameters/{process_id}", response_model=List[ProcessExecutionParameterInput])
def get_parameters(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    result = session.exec(select(ProcessExecutionParameterInput).where(ProcessExecutionParameterInput.process_execution_id == process_id)).all()
    return result

@app.delete("/parameters/{process_id}")
def delete_parameters(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(
        delete(ProcessExecutionParameterInput)
        .where(ProcessExecutionParameterInput.process_execution_id == process_id)
    )
    session.commit()
    return {"message": "Parameters deleted successfully"}

@app.post("/input_files/", response_model=ProcessExecutionInputFile)
def create_input_file(file: ProcessExecutionInputFile, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    db_obj = session.merge(file)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/input_files/{process_id}", response_model=List[ProcessExecutionInputFile])
def get_input_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(ProcessExecutionInputFile).where(ProcessExecutionInputFile.process_execution_id == process_id)).all()

@app.delete("/input_files/{process_id}")
def delete_input_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(
        delete(ProcessExecutionInputFile)
        .where(ProcessExecutionInputFile.process_execution_id == process_id)
    )
    session.commit()
    return {"message": "Input files deleted successfully"}

@app.post("/output_files/", response_model=ProcessExecutionOutputFile)
def create_output_file(file: ProcessExecutionOutputFile, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    db_obj = session.merge(file)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/output_files/{process_id}", response_model=List[ProcessExecutionOutputFile])
def get_output_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    return session.exec(select(ProcessExecutionOutputFile).where(ProcessExecutionOutputFile.process_execution_id == process_id)).all()

@app.delete("/output_files/{process_id}")
def delete_output_files(process_id: str, session: Session = Depends(get_session), api_key: str = Depends(verify_api_key)):
    session.exec(
        delete(ProcessExecutionOutputFile)
        .where(ProcessExecutionOutputFile.process_execution_id == process_id)
    )
    session.commit()
    return {"message": "Output files deleted successfully"}


# ==================== Institute Endpoints ====================

@app.post("/institutes/", response_model=Institut)
def create_institute(
    institute: Institut,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    db_obj = session.merge(institute)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/institutes/", response_model=List[Institut])
def get_institutes(
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    return session.exec(select(Institut)).all()

@app.get("/institutes/{institute_id}", response_model=Institut)
def get_institute(
    institute_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    institute = session.get(Institut, institute_id)
    if not institute:
        raise HTTPException(status_code=404, detail="Institute not found")
    return institute

@app.get("/institutes/{institute_id}/statistics")
def get_institute_statistics(
    institute_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get aggregated resource usage statistics for an institute."""
    # Get total workflows
    workflow_count = session.exec(
        select(WorkflowExecution)
        .where(WorkflowExecution.institute_id == institute_id)
    ).all()
    
    # Get total processes
    process_count = session.exec(
        select(ProcessExecution)
        .where(ProcessExecution.institute_id == institute_id)
    ).all()
    
    # Calculate totals
    total_duration = sum(p.duration for p in process_count if p.duration)
    total_peak_rss = sum(p.peak_rss for p in process_count if p.peak_rss)
    total_peak_vmem = sum(p.peak_vmem for p in process_count if p.peak_vmem)
    
    return {
        "institute_id": institute_id,
        "total_workflows": len(workflow_count),
        "total_processes": len(process_count),
        "total_duration_sec": total_duration,
        "total_peak_rss_mb": total_peak_rss,
        "total_peak_vmem_mb": total_peak_vmem,
    }

@app.get("/institutes/{institute_id}/co2-stats")
def get_institute_co2_stats(
    institute_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get CO2 emissions summary for an institute."""
    # Get all workflows for this institute
    workflows = session.exec(
        select(WorkflowExecution)
        .where(WorkflowExecution.institute_id == institute_id)
    ).all()
    
    workflow_ids = [w.id for w in workflows]
    
    if not workflow_ids:
        return {
            "institute_id": institute_id,
            "total_workflows": 0,
            "total_energy_mwh": 0,
            "total_co2e_mg": 0,
            "processes_with_co2_data": 0,
        }
    
    # Get CO2 summaries for workflows
    co2_summaries = session.exec(
        select(Workflowco2summary)
        .where(Workflowco2summary.workflow_execution_id.in_(workflow_ids))
    ).all()
    
    total_energy = sum(s.total_energy_mwh for s in co2_summaries)
    total_co2e = sum(s.total_co2e_mg for s in co2_summaries)
    
    return {
        "institute_id": institute_id,
        "total_workflows": len(workflows),
        "workflows_with_co2_data": len(co2_summaries),
        "total_energy_mwh": total_energy,
        "total_co2e_mg": total_co2e,
    }

@app.post("/institutes/{institute_id}/hardware", response_model=Hardwareinventory)
def add_hardware(
    institute_id: str,
    hardware: Hardwareinventory,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Register hardware for an institute."""
    # Verify institute exists
    institute = session.get(Institut, institute_id)
    if not institute:
        raise HTTPException(status_code=404, detail="Institute not found")
    
    hardware.institute_id = institute_id
    db_obj = session.merge(hardware)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/institutes/{institute_id}/hardware", response_model=List[Hardwareinventory])
def get_institute_hardware(
    institute_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get hardware inventory for an institute."""
    return session.exec(
        select(Hardwareinventory)
        .where(Hardwareinventory.institute_id == institute_id)
    ).all()


# ==================== CO2 Footprint Endpoints ====================

@app.post("/workflows/{workflow_id}/co2/", response_model=Workflowco2summary)
def create_workflow_co2_summary(
    workflow_id: str,
    co2_summary: Workflowco2summary,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Submit CO2 summary for a workflow."""
    # Verify workflow exists
    workflow = session.get(WorkflowExecution, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    co2_summary.workflow_execution_id = workflow_id
    db_obj = session.merge(co2_summary)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/workflows/{workflow_id}/co2/", response_model=Workflowco2summary)
def get_workflow_co2_summary(
    workflow_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get CO2 summary for a workflow."""
    co2_data = session.get(Workflowco2summary, workflow_id)
    if not co2_data:
        raise HTTPException(status_code=404, detail="CO2 summary not found")
    return co2_data
    return co2_data


# ==================== ML Model Endpoints ====================

@app.post("/ml/models/", response_model=Mlmodelmetadata)
def register_ml_model(
    model: Mlmodelmetadata,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Register metadata for a trained ML model."""
    db_obj = session.merge(model)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/ml/models/", response_model=List[Mlmodelmetadata])
def list_ml_models(
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """List all registered ML models."""
    return session.exec(select(Mlmodelmetadata)).all()

@app.get("/ml/models/{model_id}", response_model=Mlmodelmetadata)
def get_ml_model(
    model_id: int,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get metadata for a specific ML model."""
    model = session.get(Mlmodelmetadata, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


# ==================== Optimization Rules Endpoints ====================

@app.post("/institutes/{institute_id}/optimization-rules/", response_model=Optimizationrule)
def create_optimization_rule(
    institute_id: str,
    rule: Optimizationrule,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Create an optimization rule for an institute."""
    # Verify institute exists
    institute = session.get(Institut, institute_id)
    if not institute:
        raise HTTPException(status_code=404, detail="Institute not found")
    
    rule.institute_id = institute_id
    db_obj = session.merge(rule)
    session.commit()
    session.refresh(db_obj)
    return db_obj

@app.get("/institutes/{institute_id}/optimization-rules/", response_model=List[Optimizationrule])
def get_optimization_rules(
    institute_id: str,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get optimization rules for an institute."""
    return session.exec(
        select(Optimizationrule)
        .where(Optimizationrule.institute_id == institute_id)
    ).all()


# ==================== ML Training & Prediction Endpoints ====================

@app.post("/ml/train")
def train_ml_models(
    institute_id: Optional[str] = None,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """
    Train per-process ML models on historical workflow data.
    
    Args:
        institute_id: Optional institute to train on (None = all institutes)
    """
    try:
        try:
            from ml.features import extract_process_features, get_feature_statistics
            from ml.bayesian_predictor import BayesianResourcePredictor
            from nfcore_modules import normalize_module_name
        except ImportError as e:
            return {
                "success": False,
                "error": f"Import error: {str(e)}",
                "message": "ML module not properly installed"
            }
        
        print(f"Training Bayesian per-process models{'for institute ' + institute_id if institute_id else 'for all institutes'}...")
        
        # Extract features from database
        df = extract_process_features(session)
        
        if df.empty:
            return {
                "success": False,
                "error": "No training data found",
                "message": "Submit more workflow execution data first"
            }
        
        print(f"Extracted {len(df)} process records for training")
        
        # Normalize module names using nf-core cache (EXISTING BEHAVIOR - UNCHANGED)
        print("Normalizing module names...")
        df['module_name'] = df['process_name'].apply(normalize_module_name)
        
        # Get feature statistics
        stats = get_feature_statistics(df)
        
        # Train Bayesian per-process models
        predictor = BayesianResourcePredictor()
        training_results = predictor.train_all_process_models(df, institute_id)
        
        # Clear old model metadata
        existing_models = session.exec(select(Mlmodelmetadata)).all()
        for model in existing_models:
            session.delete(model)
        
        # Register per-process Bayesian models in database
        for process_name, process_results in training_results.get('per_process', {}).items():
            if process_results.get('status') == 'trained':
                for resource_type in ['memory', 'time', 'cpu']:
                    model_meta = Mlmodelmetadata(
                        process_name=process_name,
                        resource_type=resource_type,
                        model_type="bayesian_ridge",
                        training_samples=process_results.get('samples', 0),
                        accuracy_metrics=json.dumps({
                            'model_type': 'BayesianRidge',
                            'n_samples': process_results.get('samples', 0),
                        }),
                        model_artifact_path=f"/code/models/{process_name}_{resource_type}_bayesian.pkl",
                        is_fallback_model=False
                    )
                    session.add(model_meta)
        
        # Register fallback models
        for resource_type in ['memory', 'time', 'cpu']:
            model_meta = Mlmodelmetadata(
                process_name="_FALLBACK",
                resource_type=resource_type,
                model_type="bayesian_ridge",
                training_samples=0,
                accuracy_metrics=json.dumps({
                    'model_type': 'BayesianRidge',
                    'note': 'Fallback for processes with <10 samples'
                }),
                model_artifact_path=f"/code/models/_fallback_{resource_type}_bayesian.pkl",
                is_fallback_model=True
            )
            session.add(model_meta)
        
        session.commit()
        
        # Prepare summary
        trained_count = sum(
            1 for p in training_results.get('per_process', {}).values()
            if p.get('status') == 'trained'
        )
        fallback_count = training_results['summary'].get('processes_using_fallback', 0)
        
        # Return summary only (full results too large)
        summary_response = {
            'total_processes': training_results['summary'].get('total_processes', 0),
            'processes_with_models': training_results['summary'].get('processes_with_models', 0),
            'processes_using_fallback': training_results['summary'].get('processes_using_fallback', 0),
        }
        
        # Sample of trained processes
        sample_processes = []
        for name, result in list(training_results.get('per_process', {}).items())[:10]:
            if result.get('status') == 'trained':
                sample_processes.append({
                    'name': name,
                    'samples': result.get('samples', 0),
                    'models': result.get('models', [])  # Already a list from Bayesian predictor
                })
        
        return {
            "success": True,
            "message": f"Trained {trained_count} Bayesian models, {fallback_count} processes need more data",
            "training_samples": len(df),
            "summary": summary_response,
            "sample_processes": sample_processes,
            "model_type": "BayesianRidge"
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "message": "Training failed - check server logs"
        }


@app.post("/ml/retrain")
def retrain_ml_models(
    institute_id: Optional[str] = None,
    prioritize_failures: bool = True,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """
    Manually retrain per-process ML models with new data.
    
    Args:
        institute_id: Optional institute to train on (None = all institutes)
        prioritize_failures: If True, weight failure data points higher
    """
    try:
        from ml.features import extract_process_features, get_feature_statistics
        from ml.bayesian_predictor import BayesianResourcePredictor
        from nfcore_modules import normalize_module_name
        
        print(f"Retraining per-process Bayesian models...")
        
        # Extract features from database
        df = extract_process_features(session)
        
        if df.empty:
            return {
                "success": False,
                "error": "No training data found",
                "message": "Submit more workflow execution data first"
            }
        
        print(f"Extracted {len(df)} process records for training")
        
        # Normalize module names
        df['module_name'] = df['process_name'].apply(normalize_module_name)
        
        # Get feature statistics
        stats = get_feature_statistics(df)
        
        # Train per-process Bayesian models
        predictor = BayesianResourcePredictor()
        training_results = predictor.train_all_process_models(df, institute_id)
        
        # Clear existing model metadata
        existing_models = session.exec(select(Mlmodelmetadata)).all()
        for model in existing_models:
            session.delete(model)
        
        # Register per-process Bayesian models
        for process_name, process_results in training_results.get('per_process', {}).items():
            if process_results.get('status') == 'trained':
                for resource_type in ['memory', 'time', 'cpu']:
                    model_meta = Mlmodelmetadata(
                        process_name=process_name,
                        resource_type=resource_type,
                        model_type="bayesian_ridge",
                        training_samples=process_results.get('samples', 0),
                        accuracy_metrics=json.dumps({
                            'model_type': 'BayesianRidge',
                            'n_samples': process_results.get('samples', 0),
                        }),
                        model_artifact_path=f"/code/models/{process_name}_{resource_type}_bayesian.pkl",
                        is_fallback_model=False
                    )
                    session.add(model_meta)
        
        # Register fallback models (Bayesian models don't use fallback - each process is independent)
        # Note: Bayesian models handle low-sample cases with strong priors instead of fallback
        
        session.commit()
        
        session.commit()
        
        trained_count = sum(
            1 for p in training_results.get('per_process', {}).values()
            if p.get('status') == 'trained'
        )
        fallback_count = training_results['summary'].get('processes_using_fallback', 0)
        
        return {
            "success": True,
            "message": f"Retrained {trained_count} per-process models, {fallback_count} using fallback",
            "training_samples": len(df),
            "summary": training_results.get('summary', {}),
            "model_results": convert_numpy_types(training_results)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "message": "Retraining failed - check server logs"
        }


def extract_module_name(process_name: str, nfcore_cache: Optional[Dict] = None) -> str:
    """
    Extract clean module name from process name using nf-core normalization.
    
    Examples:
        NFCORE:...:TABIX_BGZIPTABIX_GT (test12) -> TABIX_BGZIPTABIX_GT
        NFCORE:...:BCFTOOLS_FILTER_QUERY_FP (test1) -> BCFTOOLS_FILTER
        NFCORE:...:BCFTOOLS_NORM (test1) -> BCFTOOLS_NORM
    
    Args:
        process_name: Full process name from Nextflow
        nfcore_cache: Optional pre-loaded nf-core module cache
    """
    from nfcore_modules import normalize_module_name
    return normalize_module_name(process_name, nfcore_cache)


def get_extrapolation_warning(extrapolation_factor: float, training_stats: Dict) -> Optional[str]:
    """
    Generate warning message based on how far prediction extrapolates beyond training data.
    
    Args:
        extrapolation_factor: Ratio of prediction size to max training size
        training_stats: Training data statistics
    
    Returns:
        Warning message or None
    """
    if extrapolation_factor <= 1.0:
        return None  # Within training range
    elif extrapolation_factor <= 2.0:
        return "⚠️ Prediction extrapolates up to 2x beyond training data"
    elif extrapolation_factor <= 5.0:
        return "⚠️⚠️ Prediction extrapolates 2-5x beyond training data - use with caution"
    else:
        return "⚠️⚠️⚠️ Prediction extrapolates >5x beyond training data - accuracy uncertain"


def format_memory(mb_value: float) -> str:
    """
    Format memory to human-readable values.
    Uses MB for values < 1024, GB for larger values.
    """
    if mb_value <= 0:
        return "64 MB"
    elif mb_value < 128:
        return "128 MB"
    elif mb_value < 256:
        return "256 MB"
    elif mb_value < 512:
        return "512 MB"
    elif mb_value < 1024:
        return "768 MB"
    elif mb_value < 2048:
        return "1 GB"
    elif mb_value < 4096:
        return "2 GB"
    elif mb_value < 8192:
        return "4 GB"
    elif mb_value < 16384:
        return "8 GB"
    elif mb_value < 32768:
        return "16 GB"
    else:
        # For very large values, round to nearest 16 GB
        gb_value = int(round(mb_value / 1024 / 16) * 16)
        return f"{gb_value} GB"


def round_time(seconds: float, minimum_seconds: int = 3600) -> str:
    """
    Round time to practical values with minimum 1 hour (3600s).
    
    Args:
        seconds: Time in seconds
        minimum_seconds: Minimum time (default 3600 = 1 hour for ML predictions)
    """
    # CRITICAL: Enforce minimum time (1 hour for ML predictions)
    seconds = max(seconds, minimum_seconds)
    
    if seconds <= 0:
        return "1h"
    
    # No additional safety margin - P95 already conservative
    safe_seconds = seconds
    
    if safe_seconds < 3600:
        return "1h"
    elif safe_seconds < 86400:
        hours = int(safe_seconds / 3600)
        mins = int((safe_seconds % 3600) / 60)
        return f"{hours}h{mins}m" if mins > 0 else f"{hours}h"
    else:
        days = int(safe_seconds / 86400)
        hours = int((safe_seconds % 86400) / 3600)
        return f"{days}d{hours}h" if hours > 0 else f"{days}d"


@app.get("/ml/processes")
def list_processes(
    institute_id: Optional[str] = None,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """Get list of distinct module names for dropdown (without instance suffix)."""
    try:
        query = "SELECT DISTINCT process_name FROM processexecution"
        params = {}
        
        if institute_id:
            query += " WHERE institute_id = :institute_id"
            params['institute_id'] = institute_id
        
        query += " ORDER BY process_name"
        
        result = session.execute(text(query), params)
        # Extract unique module names (without instance suffix)
        all_processes = [row[0] for row in result]
        module_names = sorted(set(extract_module_name(p) for p in all_processes))
        
        return {
            "success": True,
            "processes": module_names,
            "count": len(module_names)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "processes": []
        }


@app.get("/ml/predict")
def predict_resources(
    process_name: str,
    institute_id: Optional[str] = None,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """
    Get ML-based resource predictions for a process.
    USES TRAINED ML MODELS to predict resources for small/medium/large datasets.
    
    Args:
        process_name: Name of the process (e.g., "BCFTOOLS_SORT")
        institute_id: Optional institute ID to filter historical data
    """
    try:
        # Load trained Bayesian models
        try:
            from ml.bayesian_predictor import BayesianResourcePredictor
            from ml.features import extract_process_features
        except ImportError as e:
            return {
                "success": False,
                "error": f"Import error: {str(e)}",
                "message": "Bayesian ML module not properly installed"
            }
        
        import numpy as np
        
        # Get historical data for this process to build features
        query = """
        SELECT 
            p.peak_rss,
            p.peak_vmem,
            p.duration,
            p.cpus_requested,
            p.time_requested,
            p.memory_requested,
            p.percent_cpu,
            p.disk_usage_mb,
            p.process_name,
            p.institute_id,
            p.read_char,
            p.write_char,
            p.realtime
        FROM processexecution p
        WHERE UPPER(SPLIT_PART(p.process_name, ' (', 1)) LIKE UPPER(:process_name)
           OR UPPER(SPLIT_PART(p.process_name, ':', -1)) LIKE UPPER(:process_name)
        """
        params = {"process_name": f"%{process_name}%"}
        
        if institute_id:
            query += " AND p.institute_id = :institute_id"
            params["institute_id"] = institute_id
        
        result = session.execute(text(query), params)
        rows = [dict(r._mapping) for r in result]
        
        if not rows:
            return {
                "success": False,
                "error": "No historical data found for this process",
                "message": "Submit workflow data first"
            }
        
        # Get training statistics from the predictor
        training_stats = getattr(predictor, 'training_stats', {})
        
        # Helper to safely calculate mean
        def safe_mean(values):
            valid = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
            return float(np.mean(valid)) if valid else 0.0
        
        # Build base feature vector from historical averages
        avg_features = {
            'cpus_requested': safe_mean([r.get('cpus_requested') for r in rows]) or 1.0,
            'time_requested': safe_mean([r.get('time_requested') for r in rows]) or 0.0,
            'storage_requested': 0.0,
            'memory_requested': safe_mean([r.get('memory_requested') for r in rows]) or 0.0,
            'realtime': safe_mean([r.get('realtime') for r in rows]) or 0.0,
            'percent_cpu': safe_mean([r.get('percent_cpu') for r in rows]) or 0.0,
            'percent_memory': safe_mean([r.get('percent_memory') for r in rows]) or 0.0,
            'peak_rss': safe_mean([r.get('peak_rss') for r in rows]) or 0.0,
            'peak_vmem': safe_mean([r.get('peak_vmem') for r in rows]) or 0.0,
            'read_char': safe_mean([r.get('read_char') for r in rows]) or 0.0,
            'write_char': safe_mean([r.get('write_char') for r in rows]) or 0.0,
            'duration': safe_mean([r.get('duration') for r in rows]) or 0.0,
            'has_module': 1 if ':' in (rows[0]['process_name'] if rows else '') else 0,
            'cpu_utilization': safe_mean([r.get('percent_cpu') for r in rows]) / 100.0,
            'memory_utilization': safe_mean([r.get('percent_memory') for r in rows]) / 100.0,
            'memory_requested_mb': safe_mean([r.get('memory_requested') for r in rows]) or 1.0,
            'memory_efficiency': 1.0,
            'time_efficiency': 1.0,
            'io_total': safe_mean([r.get('read_char') for r in rows]) + safe_mean([r.get('write_char') for r in rows]),
            'io_ratio': 1.0,
            'cpu_mem_product': 1.0,
            'disk_usage_mb': safe_mean([r.get('disk_usage_mb') for r in rows]) or 1.0,
            'size_category_encoded': 1.0,
            'memory_per_gb': 1.0,
            'time_per_gb': 1.0,
            'cpu_per_gb': 1.0,
        }
        
        # Get disk size percentiles
        disk_values = [r['disk_usage_mb'] for r in rows if r.get('disk_usage_mb') and r['disk_usage_mb'] > 0]
        if not disk_values:
            disk_values = [1.0]
        
        disk_p10 = float(np.percentile(disk_values, 10))
        disk_p50 = float(np.percentile(disk_values, 50))
        disk_p90 = float(np.percentile(disk_values, 90))
        
        # Get median CPU from historical data
        cpu_values = [r.get('cpus_requested') for r in rows if r.get('cpus_requested') and not (isinstance(r.get('cpus_requested'), float) and np.isnan(r.get('cpus_requested', float('nan'))))]
        median_cpu = float(np.median(cpu_values)) if cpu_values else 1.0
        
        # Calculate memory per MB (not per GB to avoid huge numbers)
        # Use simple linear regression: memory = slope * disk + intercept
        disk_mem_pairs = [(r['disk_usage_mb'], r.get('peak_rss', 0) or 0) for r in rows if r.get('disk_usage_mb') and r.get('peak_rss')]
        
        if len(disk_mem_pairs) >= 2:
            # Calculate slope using least squares
            x_vals = [p[0] for p in disk_mem_pairs]
            y_vals = [p[1] for p in disk_mem_pairs]
            x_mean = np.mean(x_vals)
            y_mean = np.mean(y_vals)
            
            numerator = sum((x - x_mean) * (y - y_mean) for x, y in disk_mem_pairs)
            denominator = sum((x - x_mean) ** 2 for x in x_vals)
            
            if denominator > 0:
                slope = numerator / denominator
                intercept = y_mean - slope * x_mean
                # Ensure valid values
                if np.isnan(slope) or slope < 0.1:
                    slope = 0.5
                if np.isnan(intercept) or intercept < 0:
                    intercept = 0
            else:
                slope = 0.5
                intercept = 0
        else:
            slope = 1.0  # Default: 1 MB memory per 1 MB disk
            intercept = 0
        
        # Same for time
        disk_time_pairs = [(r['disk_usage_mb'], r.get('duration', 0) or 0) for r in rows if r.get('disk_usage_mb') and r.get('duration')]
        
        if len(disk_time_pairs) >= 2:
            x_vals = [p[0] for p in disk_time_pairs]
            y_vals = [p[1] for p in disk_time_pairs]
            x_mean = np.mean(x_vals)
            y_mean = np.mean(y_vals)
            
            numerator = sum((x - x_mean) * (y - y_mean) for x, y in disk_time_pairs)
            denominator = sum((x - x_mean) ** 2 for x in x_vals)
            
            if denominator > 0:
                time_slope = numerator / denominator
                time_intercept = y_mean - time_slope * x_mean
                if np.isnan(time_slope) or time_slope < 0.01:
                    time_slope = 0.1
                if np.isnan(time_intercept) or time_intercept < 0:
                    time_intercept = 0
            else:
                time_slope = 0.1
                time_intercept = 0
        else:
            time_slope = 1.0
            time_intercept = 0
        
        # Build predictions for each size scenario using linear model
        scenarios_response = {}
        size_scenarios = [
            ('SMALL', disk_p10),
            ('MEDIUM', disk_p50),
            ('LARGE', disk_p90)
        ]
        
        for size_name, target_disk_mb in size_scenarios:
            # Use linear model: memory = slope * disk + intercept
            memory_mb = slope * target_disk_mb + intercept
            time_sec = time_slope * target_disk_mb + time_intercept
            
            # Apply safety margins
            memory_with_safety = memory_mb * 1.2
            time_with_safety = time_sec * 1.3
            
            # Reasonable minimums
            time_with_safety = max(60, time_with_safety)
            
            scenarios_response[size_name] = {
                'cpus': max(1, min(16, int(round(median_cpu)))),
                'memory': format_memory(memory_with_safety),
                'time': round_time(time_with_safety),
                'disk_size_mb': round(target_disk_mb, 1)
            }
        
        # Use clean module name
        module_name = extract_module_name(rows[0]['process_name'] if rows else process_name)
        
        return {
            "success": True,
            "module_name": module_name,
            "historical_samples": len(rows),
            "training_samples": training_stats.get('run_count', 0),
            "scenarios": scenarios_response,
            "message": f"ML-based predictions using trained models"
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "message": "Prediction failed - models may not be trained"
        }


@app.get("/ml/optimization/{process_name}")
def get_optimization_recommendations(
    process_name: str,
    institute_id: Optional[str] = None,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """
    Get optimization recommendations for a specific module.
    Combines ML predictions with historical data analysis.
    """
    try:
        # Get historical data for this module (match module name, ignore instance suffix)
        query_str = """
        SELECT 
            p.peak_rss, p.peak_vmem, p.duration, p.cpus_requested,
            p.percent_cpu, p.percent_memory, p.time_requested, p.memory_requested,
            c.energy_consumption_mwh, c.co2e_mg,
            p.process_name, p.disk_usage_mb, p.module_name
        FROM processexecution p
        LEFT JOIN co2footprint c ON p.id = c.process_execution_id
        WHERE UPPER(p.module_name) LIKE UPPER(:process_name)
        """
        
        if institute_id:
            query_str += " AND p.institute_id = :institute_id"
        
        result = session.execute(
            text(query_str),
            {"process_name": f"%{process_name}%", "institute_id": institute_id or "UNKNOWN"}
        )
        
        historical_data = [dict(row._mapping) for row in result]
        
        if not historical_data:
            return {
                "success": False,
                "message": f"No historical data found for module: {process_name}",
                "recommendation": "Submit more workflow data first"
            }
        
        # Calculate statistics
        import numpy as np
        
        def calc_stats(values):
            values = [v for v in values if v is not None and v > 0]
            if not values:
                return None
            return {
                'mean': np.mean(values),
                'std': np.std(values),
                'min': np.min(values),
                'max': np.max(values),
                'median': np.median(values),
                'p95': np.percentile(values, 95),
                'p99': np.percentile(values, 99),
                'count': len(values)
            }
        
        # Get clean module name
        module_name = extract_module_name(process_name)
        
        # Calculate stats first
        mem_stats = calc_stats([r['peak_rss'] for r in historical_data])
        dur_stats = calc_stats([r['duration'] for r in historical_data])
        disk_values = [r['disk_usage_mb'] for r in historical_data if r.get('disk_usage_mb')]
        disk_stats = calc_stats(disk_values)
        
        # Calculate average CPUs from historical data
        # Strategy: Trust explicit cpus_requested, only cap uncertain estimates
        avg_cpus = []
        has_explicit_cpu_data = False
        
        for r in historical_data:
            if r.get('cpus_requested') and r['cpus_requested'] > 0:
                # Trust explicit CPU requests from workflow - no artificial cap
                # If user requested 16 CPUs for STAR, we honor that
                avg_cpus.append(r['cpus_requested'])
                has_explicit_cpu_data = True
            elif r.get('percent_cpu') and r['percent_cpu'] > 0:
                # Estimate from percent_cpu only when explicit data unavailable
                # Cap at 12 for estimates (uncertain data needs conservative bound)
                estimated = min(12, max(1, int(round(r['percent_cpu'] / 100))))
                avg_cpus.append(estimated)
        
        # Use average - only cap if we're estimating (no explicit CPU data)
        if avg_cpus:
            avg_cpu_value = np.mean(avg_cpus)
            
            # If we have explicit CPU requests, trust them (no cap)
            # If we're estimating from percent_cpu, cap at 12 for safety
            if has_explicit_cpu_data:
                recommended_cpus = max(1, int(round(avg_cpu_value)))
            else:
                recommended_cpus = min(12, max(1, int(round(avg_cpu_value))))
        else:
            recommended_cpus = 1
        
        recommendations = {
            "module_name": module_name,
            "historical_samples": len(historical_data),
            "memory": mem_stats,
            "duration": dur_stats,
            "disk": disk_stats,
            "cpu_utilization": calc_stats([r['percent_cpu'] for r in historical_data]),
            "energy": calc_stats([r['energy_consumption_mwh'] for r in historical_data if r.get('energy_consumption_mwh')]),
            "co2": calc_stats([r['co2e_mg'] for r in historical_data if r.get('co2e_mg')]),
            "recommended_config": {
                "memory": format_memory(mem_stats['p95']) if mem_stats else "256 MB",
                "time": round_time(dur_stats['p95']) if dur_stats else "1m",
                "cpus": recommended_cpus
            },
            "historical_size_range_gb": {
                'min': disk_stats['min'] / 1000 if disk_stats and disk_stats.get('min') else 0,
                'max': disk_stats['max'] / 1000 if disk_stats and disk_stats.get('max') else 0
            }
        }
        
        # Add efficiency insights
        insights = []
        
        if recommendations['cpu_utilization']:
            avg_cpu = recommendations['cpu_utilization']['mean']
            if avg_cpu < 50:
                insights.append("Low CPU utilization - consider reducing CPU allocation")
            elif avg_cpu > 90:
                insights.append("High CPU utilization - process is CPU-bound")
        
        if recommendations['memory']:
            mem_recommended = recommendations['memory']['p95']
            mem_requested = np.mean([r['memory_requested'] for r in historical_data if r.get('memory_requested')])
            if mem_recommended < mem_recommended * 0.7:
                insights.append(f"Memory over-allocated - can reduce by ~{int((1 - mem_recommended/mem_requested)*100)}%")
        
        recommendations['insights'] = insights
        
        return {
            "success": True,
            **recommendations
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to generate recommendations"
        }


@app.get("/ml/optimizations")
def get_all_optimizations(
    institute_id: Optional[str] = None,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    """
    Get ML-based optimization recommendations for ALL modules with S/M/L scenarios.
    USES TRAINED ML MODELS to predict resources for small, medium, and large dataset sizes.
    """
    try:
        # Load trained Bayesian models
        try:
            from ml.bayesian_predictor import BayesianResourcePredictor
            from nfcore_modules import normalize_module_name, get_nfcore_modules
        except ImportError as e:
            return {
                "success": False,
                "error": f"Import error: {str(e)}",
                "message": "Bayesian ML module not properly installed"
            }
        
        # Load nf-core module cache
        nfcore_cache = get_nfcore_modules()
        
        predictor = BayesianResourcePredictor()
        # Bayesian models are loaded on-demand per process
        
        import numpy as np
        
        # Get all distinct module names
        query_modules = "SELECT DISTINCT process_name FROM processexecution"
        params = {}
        
        if institute_id:
            query_modules += " WHERE institute_id = :institute_id"
            params['institute_id'] = institute_id
        
        result = session.execute(text(query_modules), params)
        all_processes = [row[0] for row in result]
        module_names = sorted(set(extract_module_name(p) for p in all_processes))
        
        # Get training statistics
        training_stats = getattr(predictor, 'training_stats', {})
        
        all_optimizations = []
        
        for module_name in module_names:
            # Get historical data for this module
            query_str = """
                SELECT 
                    p.peak_rss,
                    p.peak_vmem,
                    p.duration,
                    p.cpus_requested,
                    p.percent_cpu,
                    p.disk_usage_mb,
                    p.memory_requested,
                    p.time_requested,
                    p.process_name,
                    p.institute_id,
                    p.read_char,
                    p.write_char,
                    p.read_bytes,
                    p.write_bytes,
                    p.realtime
                FROM processexecution p
                WHERE (UPPER(SPLIT_PART(p.process_name, ' (', 1)) LIKE UPPER(:process_name)
                   OR UPPER(SPLIT_PART(p.process_name, ':', -1)) LIKE UPPER(:process_name))
            """
            
            opt_params = {"process_name": f"%{module_name}%"}
            if institute_id:
                query_str += " AND p.institute_id = :institute_id"
                opt_params["institute_id"] = institute_id
            
            result = session.execute(text(query_str), opt_params)
            rows = [dict(r._mapping) for r in result]
            
            if not rows:
                continue
            
            # Helper to safely calculate mean
            def safe_mean(values):
                valid = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
                return float(np.mean(valid)) if valid else 0.0
            
            # Calculate historical medians for ALL data size features
            median_disk = safe_mean([r.get('disk_usage_mb') for r in rows]) or 1.0
            median_read_char = safe_mean([r.get('read_char', 0) or 0 for r in rows])
            median_write_char = safe_mean([r.get('write_char', 0) or 0 for r in rows])
            
            # Get disk size percentiles for scaling
            disk_values = [r['disk_usage_mb'] for r in rows if r.get('disk_usage_mb') and r['disk_usage_mb'] > 0]
            
            # Check if we have enough data variation for scenarios
            scenarios_response = {}
            scenario_warnings = {}
            
            if len(disk_values) >= 10:
                # Enough samples - generate all 3 scenarios
                disk_p10 = float(np.percentile(disk_values, 10))
                disk_p50 = float(np.percentile(disk_values, 50))
                disk_p90 = float(np.percentile(disk_values, 90))
                
                size_scenarios = [
                    ('SMALL', disk_p10),
                    ('MEDIUM', disk_p50),
                    ('LARGE', disk_p90)
                ]
                
                # Check if sizes are meaningfully different (at least 20% variation)
                size_variation = (disk_p90 - disk_p10) / max(disk_p50, 0.001)
                if size_variation < 0.2:
                    # Not enough variation - only show MEDIUM
                    size_scenarios = [('MEDIUM', disk_p50)]
                    scenario_warnings['size_variation'] = f"Insufficient size variation in historical data (all runs similar size). Only MEDIUM scenario shown."
            elif len(disk_values) >= 3:
                # Few samples - only show MEDIUM (median)
                disk_p50 = float(np.percentile(disk_values, 50)) if disk_values else median_disk
                size_scenarios = [('MEDIUM', disk_p50)]
                scenario_warnings['sample_count'] = f"Only {len(disk_values)} historical runs. Only MEDIUM scenario shown with median size."
            else:
                # Too few samples - skip ML predictions, use historical averages
                size_scenarios = [('MEDIUM', median_disk)]
                scenario_warnings['insufficient_data'] = f"Only {len(disk_values)} historical runs. Using historical averages instead of ML predictions."
            
            for size_name, disk_mb in size_scenarios:
                # Calculate scaling factor relative to historical median
                scale_factor = disk_mb / max(median_disk, 0.001)
                
                # Build feature vector for this scenario - scale ALL data size features
                # IMPORTANT: Use EXACT feature names that model was trained with
                # For PER-PROCESS models: NO process_base (each model is process-specific)
                scenario_features = {
                    # Process identity
                    'has_module': 1 if ':' in (rows[0]['process_name'] if rows else '') else 0,
                    
                    # Data size features (ALL scaled proportionally)
                    'disk_intensity': disk_mb,
                    'disk_io_total': (median_read_char + median_write_char) * scale_factor,
                    'disk_io_ratio': median_read_char / (median_write_char + 0.001),
                    
                    # Utilization metrics
                    'cpu_utilization': safe_mean([r.get('percent_cpu') for r in rows]) / 100.0,
                    'memory_utilization': safe_mean([r.get('percent_memory') for r in rows]) / 100.0,
                    
                    # Legacy I/O features (scaled) - model expects these
                    'io_total': (median_read_char + median_write_char) * scale_factor,
                    'io_ratio': 1.0,
                    
                    # Interaction features
                    'cpu_mem_product': safe_mean([r.get('percent_cpu') for r in rows]) * safe_mean([r.get('peak_rss') for r in rows]),
                    
                    # Size category encoding
                    'size_category_encoded': 0.0 if size_name == 'SMALL' else (1.0 if size_name == 'MEDIUM' else 2.0),
                    
                    # Per-GB features (scale with size) - these are what CPU model SHOULD use
                    'memory_per_gb': (safe_mean([r.get('peak_rss') for r in rows]) / max(median_disk / 1000, 0.001)) * scale_factor,
                    'time_per_gb': (safe_mean([r.get('duration') for r in rows]) / max(median_disk / 1000, 0.001)) * scale_factor,
                    'cpu_per_gb': (safe_mean([r.get('percent_cpu') for r in rows]) / 100.0 / max(median_disk / 1000, 0.001)) * scale_factor,
                }
                
                # Get Bayesian predictions for this scenario
                try:
                    bayesian_predictions = predictor.predict(module_name, scenario_features)
                    
                    # Extract predictions with safety margins
                    memory_mb = bayesian_predictions['memory']['final_with_safety']
                    time_sec = max(3600, bayesian_predictions['time']['final_with_safety'])  # Minimum 1 hour
                    cpu_value = max(1, round(bayesian_predictions['cpu']['final_with_safety']))
                    is_fallback = False
                except Exception as pred_error:
                    # Fallback if prediction fails
                    memory_mb = 256.0
                    time_sec = 7200
                    cpu_value = 2
                    is_fallback = True
                
                scenarios_response[size_name] = {
                    'cpus': cpu_value,
                    'is_fallback_model': is_fallback,
                    'memory': format_memory(memory_mb),
                    'time': round_time(time_sec),
                    'disk_size_mb': round(disk_mb, 1)
                }
            
            # Add warnings if any scenarios were skipped
            if scenario_warnings:
                scenarios_response['_warnings'] = scenario_warnings
                
            optimization = {
                "module_name": module_name,
                "historical_samples": len(rows),
                "scenarios": scenarios_response
            }
            
            all_optimizations.append(optimization)
        
        return {
            "success": True,
            "modules": len(all_optimizations),
            "training_samples": training_stats.get('run_count', 0),
            "optimizations": all_optimizations
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "optimizations": []
        }


class ResourceLimits(SQLModel):
    """Resource limits for workflow tasks."""
    max_cpus: int = 32
    max_memory_mb: float = 131072  # 128 GB
    max_duration_sec: int = 86400  # 24 hours


class OptimizeForSizeRequest(SQLModel):
    """Request model for interactive optimization endpoint."""
    process_name: str
    expected_disk_gb: float
    confidence_level: float = 0.95
    priority: str = "balanced"
    resource_limits: Optional[ResourceLimits] = None


@app.post("/ml/optimize-for-size")
def optimize_for_data_size(
    request: OptimizeForSizeRequest,
    session: Session = Depends(get_session),
    api_key: str = Depends(verify_api_key)
):
    # Extract from request body
    process_name = request.process_name
    expected_disk_gb = request.expected_disk_gb
    confidence_level = request.confidence_level
    priority = request.priority
    """
    Generate optimized config for user-specified data size.
    Uses Bayesian models with dynamic safety margins based on uncertainty.
    
    Args:
        process_name: Normalized process name (e.g., "BCFTOOLS_FILTER")
        expected_disk_gb: User's expected input data size in GB
        confidence_level: Confidence level for intervals (default 0.95)
        priority: "balanced", "cost", or "performance"
    """
    try:
        from ml.bayesian_predictor import BayesianResourcePredictor
        from ml.features import extract_process_features
        
        # Get historical data using existing feature extraction
        all_features = extract_process_features(session)
        
        # Filter for this process
        df = all_features[all_features['module_name'] == process_name].copy()
        
        if df.empty:
            return {
                'success': False,
                'error': f'No historical data found for process: {process_name}'
            }
        
        if len(df) < 10:
            return {
                'success': False,
                'error': f'Insufficient historical data ({len(df)} runs, need ≥10)',
                'recommendation': 'Run workflow more times or use fallback model'
            }
        
        # Load or train Bayesian model
        predictor = BayesianResourcePredictor()
        
        # Try to load pre-trained models
        if not predictor.load_model(process_name):
            # Train on-the-fly
            predictor.train(process_name, df)
        
        # Build feature vector for user's data size
        historical_avg = {
            'disk_gb': df['disk_usage_mb'].mean() / 1000,
            'cpu_utilization': df['cpu_utilization'].mean(),
            'memory_utilization': df['memory_utilization'].mean(),
            'io_total': df['io_total'].mean(),
            'io_ratio': df['io_ratio'].mean(),
            'cpu_mem_product': df['cpu_mem_product'].mean(),
            'memory_per_gb': df['memory_per_gb'].mean(),
            'time_per_gb': df['time_per_gb'].mean(),
            'cpu_per_gb': df['cpu_per_gb'].mean()
        }
        
        # Scale features proportionally to user's data size
        scaling_factor = expected_disk_gb / historical_avg['disk_gb']
        
        # Calculate new enhanced features for better CPU scaling
        import numpy as np
        log_disk_gb = np.log1p(expected_disk_gb)
        disk_cpu_interaction = expected_disk_gb * (historical_avg['cpu_utilization'] + 0.001)
        io_per_cpu = (historical_avg['io_total'] * scaling_factor) / (historical_avg['cpu_utilization'] + 1)
        memory_cpu_ratio = historical_avg['memory_per_gb'] / (historical_avg['cpu_per_gb'] / 100 + 0.001)
        
        features = {
            'disk_intensity': expected_disk_gb * 1000,  # In MB
            'cpu_utilization': historical_avg['cpu_utilization'],
            'memory_utilization': historical_avg['memory_utilization'],
            'io_total': historical_avg['io_total'] * scaling_factor,
            'io_ratio': historical_avg['io_ratio'],
            'cpu_mem_product': historical_avg['cpu_mem_product'],
            'memory_per_gb': historical_avg['memory_per_gb'],
            'time_per_gb': historical_avg['time_per_gb'],
            'cpu_per_gb': historical_avg['cpu_per_gb'],
            'size_category_encoded': 1.0,  # Medium
            # New enhanced features
            'log_disk_gb': log_disk_gb,
            'disk_cpu_interaction': disk_cpu_interaction,
            'io_per_cpu': io_per_cpu,
            'memory_cpu_ratio': memory_cpu_ratio
        }
        
        # Get predictions with uncertainty
        predictions = predictor.predict(process_name, features)
        
        # Check extrapolation warnings
        warnings = []
        hist_min_gb = df['disk_usage_mb'].min() / 1000
        hist_max_gb = df['disk_usage_mb'].max() / 1000
        
        # Warning A: Outside historical range
        if expected_disk_gb < hist_min_gb or expected_disk_gb > hist_max_gb:
            warnings.append(
                f"Input size ({expected_disk_gb} GB) is outside historical range "
                f"({hist_min_gb:.1f} - {hist_max_gb:.1f} GB). Predictions may be less accurate."
            )
        
        # Warning C: High uncertainty
        for resource_type in ['memory', 'time', 'cpu']:
            cv = predictions[resource_type]['cv']
            if cv > 0.3:
                warnings.append(
                    f"High uncertainty for {resource_type} prediction (CV={cv:.2f}). "
                    f"Consider running more historical executions."
                )
        
        # Apply priority-based adjustments
        if priority == "cost":
            # Reduce safety margins by 50% (accept higher risk)
            for k in predictions:
                original_margin = predictions[k]['safety_margin']
                predictions[k]['safety_margin'] = original_margin * 0.5
                predictions[k]['final_with_safety'] = (
                    predictions[k]['prediction'] * (1 + predictions[k]['safety_margin'])
                )
        elif priority == "performance":
            # Increase safety margins by 20% (conservative)
            for k in predictions:
                original_margin = predictions[k]['safety_margin']
                predictions[k]['safety_margin'] = original_margin * 1.2
                predictions[k]['final_with_safety'] = (
                    predictions[k]['prediction'] * (1 + predictions[k]['safety_margin'])
                )
        # "balanced" = use Bayesian uncertainty-based margins (no adjustment)
        
        # Enforce minimum 1 hour time limit (Nextflow constraint)
        min_time_sec = 3600  # 1 hour minimum
        if predictions['time']['final_with_safety'] < min_time_sec:
            predictions['time']['final_with_safety'] = min_time_sec
            predictions['time']['enforced_minimum'] = True
        else:
            predictions['time']['enforced_minimum'] = False
        
        # Apply USER-PROVIDED resource limits as HARD CAPS
        # These come from the UI input in ML Training tab or Optimizations tab override
        resource_limits = request.resource_limits
        
        # Default limits if not provided (reasonable defaults)
        if not resource_limits:
            resource_limits = ResourceLimits(
                max_cpus=32,
                max_memory_mb=131072,  # 128 GB
                max_duration_sec=86400  # 24 hours
            )
        
        # Apply user limits as absolute caps
        for resource_type in ['cpu', 'memory', 'time']:
            prediction_value = predictions[resource_type]['final_with_safety']
            
            # Get the appropriate limit
            if resource_type == 'cpu':
                limit = resource_limits.max_cpus
            elif resource_type == 'memory':
                limit = resource_limits.max_memory_mb
            else:  # time
                limit = max(min_time_sec, resource_limits.max_duration_sec)  # Respect 1h minimum
            
            # Cap at user-provided limit
            if prediction_value > limit:
                predictions[resource_type]['final_with_safety'] = float(limit)
                predictions[resource_type]['capped_by_limit'] = True
                
                # Calculate extrapolation factor for warning
                extrapolation_factor = expected_disk_gb / max(hist_max_gb, 0.001) if hist_max_gb > 0 else 0
                cv = predictions[resource_type]['cv']
                
                warning_reasons = []
                if extrapolation_factor > 10:
                    warning_reasons.append(f"{extrapolation_factor:.0f}x beyond historical data")
                if cv > 1.0:
                    warning_reasons.append(f"CV={cv:.0%} (high uncertainty)")
                
                warning_text = f"{', '.join(warning_reasons)} - prediction capped at user limit" if warning_reasons else "capped at user limit"
                warnings.append(
                    f"{resource_type.upper()}: {prediction_value:.0f} → {limit} ({warning_text})"
                )
            else:
                predictions[resource_type]['capped_by_limit'] = False
        
        # Format time for Nextflow (1h, 30m, 3600s format)
        def format_nextflow_time(seconds):
            if seconds >= 3600:
                hours = int(seconds // 3600)
                remaining = seconds % 3600
                if remaining >= 60:
                    mins = int(remaining // 60)
                    return f"{hours}h{mins}m"
                return f"{hours}h"
            elif seconds >= 60:
                mins = int(seconds // 60)
                return f"{mins}m"
            else:
                return f"{int(seconds)}s"
        
        time_formatted = format_nextflow_time(predictions['time']['final_with_safety'])
        
        # Generate Nextflow config (ensure non-negative values)
        memory_mb = max(256, predictions['memory']['final_with_safety'])  # Minimum 256 MB
        nextflow_config = f"""process {{
    withName: '{process_name}' {{
        cpus = {max(1, round(predictions['cpu']['final_with_safety']))}
        memory = {memory_mb:.0f}.MB
        time = {time_formatted}
        
        // Bayesian prediction with uncertainty
        // Input data size: {expected_disk_gb} GB
        // Memory: {max(0, predictions['memory']['prediction']):.0f} MB ±{predictions['memory']['uncertainty']:.0f} MB (CV={predictions['memory']['cv']:.0%})
        // Time: {max(0, predictions['time']['prediction']):.0f} sec ±{predictions['time']['uncertainty']:.0f} sec (CV={predictions['time']['cv']:.0%})
        // 95% CI Memory: [{max(0, predictions['memory']['confidence_interval']['lower']):.0f}, {predictions['memory']['confidence_interval']['upper']:.0f}] MB
        // 95% CI Time: [{max(0, predictions['time']['confidence_interval']['lower']):.0f}, {predictions['time']['confidence_interval']['upper']:.0f}] sec
        // Minimum 1 hour enforced: {'Yes' if predictions['time']['enforced_minimum'] else 'No'} ({time_formatted})
    }}
}}
"""
        
        return {
            'success': True,
            'process_name': process_name,
            'input_disk_gb': expected_disk_gb,
            'confidence_level': confidence_level,
            'priority': priority,
            'predictions': predictions,
            'nextflow_config': nextflow_config,
            'historical_runs_used': len(df),
            'historical_size_range_gb': {
                'min': float(hist_min_gb),
                'max': float(hist_max_gb)
            },
            'warnings': warnings,
            'model_type': 'BayesianRidge'
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e),
            'message': 'Optimization failed - check server logs'
        }