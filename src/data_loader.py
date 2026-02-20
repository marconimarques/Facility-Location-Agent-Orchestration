"""Data loading and validation module using Pydantic."""

from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
from pydantic import BaseModel, Field, field_validator


class CollectionPoint(BaseModel):
    """Raw material collection point with volumes and prices."""
    site_id: str
    company: str
    plant: str
    volumes: Dict[str, float]  # {'A': 1200.5, 'B': 800.3, ...}
    prices: Dict[str, float]   # {'A': 45.5, 'B': 38.2, ...}

    @field_validator('volumes', 'prices')
    @classmethod
    def validate_materials(cls, v):
        """Ensure all 5 material types present."""
        required = {'A', 'B', 'C', 'D', 'E'}
        if set(v.keys()) != required:
            raise ValueError(f"Must have materials: {required}")
        return v


class PortDetails(BaseModel):
    """Port operational and sea freight costs."""
    port_name: str
    operational_cost: float = Field(gt=0, description="$/ton")
    sea_freight_cost: float = Field(gt=0, description="$/ton")


class ProductionParameters(BaseModel):
    """Production target, yield factors, consumption limits."""
    target_tons: float = Field(gt=0)
    yield_factors: Dict[str, float]  # {'A': 0.21, 'B': 0.17, ...}
    max_consumption: Dict[str, float]  # {'A': 1.0, 'E': 0.5}

    @field_validator('yield_factors', 'max_consumption')
    @classmethod
    def validate_percentages(cls, v):
        """Ensure percentages are in valid range."""
        for key, val in v.items():
            if not (0 < val <= 1):
                raise ValueError(f"{key}: {val} must be in (0, 1]")
        return v


class OptimizationData(BaseModel):
    """Complete validated dataset for optimization."""
    collection_points: List[CollectionPoint]
    inbound_freight: Dict[Tuple[str, str], float]
    material_e_freight: float  # Fixed freight cost for MaterialE ($/ton)
    outbound_freight: Dict[Tuple[str, str], float]
    ports: List[PortDetails]
    production_params: ProductionParameters

    # Optional fields for what-if scenarios
    forced_facility: Optional[str] = None
    forced_ports: Optional[List[str]] = None

    class Config:
        arbitrary_types_allowed = True

    @field_validator('collection_points')
    @classmethod
    def validate_unique_sites(cls, v):
        """Ensure no duplicate site IDs."""
        site_ids = [cp.site_id for cp in v]
        if len(site_ids) != len(set(site_ids)):
            raise ValueError("Duplicate site IDs found")
        return v


def load_raw_material_details(filepath: Path) -> List[CollectionPoint]:
    """Load raw material details from Excel.

    Args:
        filepath: Path to INPUT_RawMaterial_Details.xlsx

    Returns:
        List of CollectionPoint objects
    """
    df = pd.read_excel(filepath)

    # Validate required columns
    required_cols = ['Company', 'Plant']
    volume_cols = [f'RawMaterial{m}_Volume' for m in ['A', 'B', 'C', 'D', 'E']]
    price_cols = [f'RawMaterial{m}_Price' for m in ['A', 'B', 'C', 'D', 'E']]
    required_cols.extend(volume_cols)
    required_cols.extend(price_cols)

    missing = set(required_cols) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {filepath.name}: {missing}")

    # Convert to CollectionPoint objects
    points = []
    for idx, row in df.iterrows():
        site_id = f"{row['Company']}_{row['Plant']}"

        volumes = {
            'A': float(row['RawMaterialA_Volume']),
            'B': float(row['RawMaterialB_Volume']),
            'C': float(row['RawMaterialC_Volume']),
            'D': float(row['RawMaterialD_Volume']),
            'E': float(row['RawMaterialE_Volume'])
        }

        prices = {
            'A': float(row['RawMaterialA_Price']),
            'B': float(row['RawMaterialB_Price']),
            'C': float(row['RawMaterialC_Price']),
            'D': float(row['RawMaterialD_Price']),
            'E': float(row['RawMaterialE_Price'])
        }

        point = CollectionPoint(
            site_id=site_id,
            company=row['Company'],
            plant=row['Plant'],
            volumes=volumes,
            prices=prices
        )
        points.append(point)

    return points


def load_inbound_freight(filepath: Path) -> Dict[Tuple[str, str], float]:
    """Load inbound freight cost matrix.

    Args:
        filepath: Path to INPUT_RawMaterial_Distance_Matrix_And_Freight.xlsx

    Returns:
        Dictionary mapping (origin, destination) -> freight cost
    """
    # Read freight matrix sheet (first column contains site names)
    df = pd.read_excel(filepath, sheet_name='RawMaterial_Freight_Matrix', index_col=0)

    # Convert to dictionary
    freight_dict = {}

    # Iterate through all origin-destination pairs
    for origin in df.index:
        for destination in df.columns:
            cost = float(df.loc[origin, destination])
            freight_dict[(str(origin), str(destination))] = cost

    return freight_dict


def load_material_e_freight(filepath: Path) -> float:
    """Load special MaterialE freight cost (fixed at 50 miles).

    Args:
        filepath: Path to INPUT_RawMaterial_Distance_Matrix_And_Freight.xlsx

    Returns:
        Fixed freight cost for MaterialE ($/ton)
    """
    # Read MaterialE special freight sheet
    df = pd.read_excel(filepath, sheet_name='RawMaterialE_Freight')

    # Return the fixed freight cost for MaterialE
    return float(df.loc[0, 'US$/ton'])


def load_outbound_freight(filepath: Path) -> Dict[Tuple[str, str], float]:
    """Load outbound freight costs to ports.

    Args:
        filepath: Path to INPUT_FinishedProd_Distance_Matrix_And_Freight.xlsx

    Returns:
        Dictionary mapping (site, port) -> freight cost
    """
    # Read freight to ports sheet (first column contains site names)
    df = pd.read_excel(filepath, sheet_name='FinishedProd_Freight_To_Ports', index_col=0)

    freight_dict = {}

    # Iterate through sites (rows) and ports (columns)
    for site in df.index:
        for port in df.columns:
            cost = float(df.loc[site, port])
            freight_dict[(str(site), str(port))] = cost

    return freight_dict


def load_port_details(filepath: Path) -> List[PortDetails]:
    """Load port operational and sea freight costs.

    Args:
        filepath: Path to INPUT_Port_Details.xlsx

    Returns:
        List of PortDetails objects
    """
    df = pd.read_excel(filepath)

    ports = []
    for idx, row in df.iterrows():
        port = PortDetails(
            port_name=row['Port_Name'],
            operational_cost=float(row['Port_Operational_Cost']),
            sea_freight_cost=float(row['Sea_Freight_Cost'])
        )
        ports.append(port)

    return ports


def load_production_parameters(filepath: Path) -> ProductionParameters:
    """Load production parameters, yield factors, and constraints.

    Args:
        filepath: Path to INPUT_Demand_Yield_Limits.xlsx

    Returns:
        ProductionParameters object
    """
    df = pd.read_excel(filepath)

    # Convert to dictionary for easy access
    params_dict = {}
    for idx, row in df.iterrows():
        params_dict[row.iloc[0]] = row.iloc[1]

    # Extract target production
    target = float(params_dict['FinishedProd_Production_Tons_Target'])

    # Extract yield factors
    yield_factors = {
        'A': float(params_dict['Yield_Factor_RawMaterialA']),
        'B': float(params_dict['Yield_Factor_RawMaterialB']),
        'C': float(params_dict['Yield_Factor_RawMaterialC']),
        'D': float(params_dict['Yield_Factor_RawMaterialD']),
        'E': float(params_dict['Yield_Factor_RawMaterialE'])
    }

    # Extract max consumption limits
    max_consumption = {
        'A': float(params_dict['Max_Consumption_RawMaterialA']),
        'B': float(params_dict['Max_Consumption_RawMaterialB']),
        'C': float(params_dict['Max_Consumption_RawMaterialC']),
        'D': float(params_dict['Max_Consumption_RawMaterialD']),
        'E': float(params_dict['Max_Consumption_RawMaterialE'])
    }

    return ProductionParameters(
        target_tons=target,
        yield_factors=yield_factors,
        max_consumption=max_consumption
    )


def load_all_data(data_dir: str = 'data') -> 'OptimizationData':
    """Load all input Excel files and return validated data.

    Args:
        data_dir: Directory containing Excel files

    Returns:
        OptimizationData object with all validated data

    Raises:
        FileNotFoundError: If required Excel files are missing
        ValueError: If data validation fails
    """
    data_path = Path(data_dir)

    if not data_path.exists():
        raise FileNotFoundError(f"Data directory not found: {data_path}")

    # Load all data files
    collection_points = load_raw_material_details(
        data_path / 'INPUT_RawMaterial_Details.xlsx'
    )

    inbound_freight = load_inbound_freight(
        data_path / 'INPUT_RawMaterial_Distance_Matrix_And_Freight.xlsx'
    )

    material_e_freight = load_material_e_freight(
        data_path / 'INPUT_RawMaterial_Distance_Matrix_And_Freight.xlsx'
    )

    outbound_freight = load_outbound_freight(
        data_path / 'INPUT_FinishedProd_Distance_Matrix_And_Freight.xlsx'
    )

    ports = load_port_details(
        data_path / 'INPUT_Port_Details.xlsx'
    )

    production_params = load_production_parameters(
        data_path / 'INPUT_Demand_Yield_Limits.xlsx'
    )

    # Validate site ID consistency
    site_ids_from_details = set(cp.site_id for cp in collection_points)

    # Get unique sites from freight matrix
    freight_sites = set()
    for (s1, s2) in inbound_freight.keys():
        freight_sites.add(s1)
        freight_sites.add(s2)

    # Check for mismatches
    if site_ids_from_details != freight_sites:
        missing_in_freight = site_ids_from_details - freight_sites
        missing_in_details = freight_sites - site_ids_from_details

        error_msg = "Site ID mismatch between Excel files:\n"
        if missing_in_freight:
            error_msg += f"  Sites in Details but NOT in Freight Matrix: {missing_in_freight}\n"
        if missing_in_details:
            error_msg += f"  Sites in Freight Matrix but NOT in Details: {missing_in_details}\n"
        error_msg += "\nPlease check for typos in site names (Company_Plant format)."

        raise ValueError(error_msg)

    # Construct and validate complete dataset
    data = OptimizationData(
        collection_points=collection_points,
        inbound_freight=inbound_freight,
        material_e_freight=material_e_freight,
        outbound_freight=outbound_freight,
        ports=ports,
        production_params=production_params
    )

    return data


def check_production_feasibility(
    data: 'OptimizationData',
    exclude_material_e: bool = False
) -> Dict:
    """Check if production target is achievable with available raw materials.

    Args:
        data: OptimizationData object with all input data
        exclude_material_e: If True, exclude MaterialE from calculation

    Returns:
        Dictionary with feasibility details

    Raises:
        ValueError: If production target is not feasible
    """
    # Calculate total available volumes for each material type
    material_availability = {'A': 0.0, 'B': 0.0, 'C': 0.0, 'D': 0.0, 'E': 0.0}

    for cp in data.collection_points:
        for material, volume in cp.volumes.items():
            if exclude_material_e and material == 'E':
                continue
            material_availability[material] += volume

    # Get yield factors and max consumption limits
    yield_factors = data.production_params.yield_factors
    max_consumption = data.production_params.max_consumption
    target = data.production_params.target_tons

    # Calculate maximum achievable production using greedy approach
    # Sort materials by yield factor (descending) to prioritize high-yield materials
    materials_by_yield = sorted(
        [(m, yield_factors[m], material_availability[m], max_consumption[m])
         for m in ['A', 'B', 'C', 'D', 'E']
         if not (exclude_material_e and m == 'E')],
        key=lambda x: x[1],
        reverse=True
    )

    # Try to calculate maximum production
    total_available = sum(material_availability[m] for m in ['A', 'B', 'C', 'D', 'E']
                         if not (exclude_material_e and m == 'E'))

    if total_available == 0:
        raise ValueError(
            "FEASIBILITY CHECK FAILED: No raw material available.\n"
            "  Please check INPUT_RawMaterial_Details.xlsx for volume data."
        )

    # Calculate theoretical maximum production
    max_production_unconstrained = 0.0
    for material, yld, available, max_cons in materials_by_yield:
        max_production_unconstrained += yld * available

    # Use greedy allocation to check against consumption constraints
    remaining_target = target
    allocated_materials = {}
    total_consumed = 0.0

    max_iterations = 1000
    iteration = 0

    while remaining_target > 0.001 and iteration < max_iterations:
        iteration += 1
        made_progress = False

        for material, yld, available, max_cons in materials_by_yield:
            if material not in allocated_materials:
                allocated_materials[material] = 0.0

            current_total = sum(allocated_materials.values())

            max_by_consumption = max_cons * (current_total + available) - allocated_materials[material]
            max_by_availability = available - allocated_materials[material]
            max_by_target = remaining_target / yld if yld > 0 else 0

            can_allocate = min(max_by_consumption, max_by_availability, max_by_target)

            if can_allocate > 0.01:
                allocated_materials[material] += can_allocate
                remaining_target -= can_allocate * yld
                total_consumed += can_allocate
                made_progress = True

        if not made_progress:
            break

    # Calculate actual achievable production
    achievable_production = sum(
        allocated_materials.get(m, 0.0) * yield_factors[m]
        for m in ['A', 'B', 'C', 'D', 'E']
        if not (exclude_material_e and m == 'E')
    )

    # Check feasibility
    deficit = target - achievable_production

    if deficit > 0.01:  # Allow small numerical tolerance
        error_msg = (
            f"FEASIBILITY CHECK FAILED: Insufficient raw material to meet production target.\n\n"
            f"  Production Target: {target:,.2f} tons\n"
            f"  Maximum Achievable: {achievable_production:,.2f} tons\n"
            f"  Deficit: {deficit:,.2f} tons ({deficit/target*100:.1f}% short)\n\n"
            f"Raw Material Availability:\n"
        )

        for material in ['A', 'B', 'C', 'D', 'E']:
            if exclude_material_e and material == 'E':
                continue
            error_msg += (
                f"  Material {material}: {material_availability[material]:,.2f} tons available "
                f"(yield: {yield_factors[material]:.1%}, max usage: {max_consumption[material]:.1%})\n"
            )

        error_msg += (
            f"\nTotal Available Raw Material: {total_available:,.2f} tons\n"
            f"Total Raw Material Needed (estimate): {total_consumed + deficit/0.2:,.2f} tons\n\n"
            f"Possible solutions:\n"
            f"  1. Reduce production target in INPUT_Demand_Yield_Limits.xlsx\n"
            f"  2. Increase raw material availability in INPUT_RawMaterial_Details.xlsx\n"
            f"  3. Adjust yield factors or max consumption limits in INPUT_Demand_Yield_Limits.xlsx"
        )

        raise ValueError(error_msg)

    # Return feasibility details
    return {
        'feasible': True,
        'target': target,
        'achievable': achievable_production,
        'margin': achievable_production - target,
        'total_available': total_available,
        'material_allocation': allocated_materials
    }
