"""Jenkins log loader with hierarchical segmentation.

Segments Jenkins console logs according to pipeline structure.
All phase detection and stage mapping is driven by PipelineModel configuration.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Generator, List, Optional, TextIO, Union

from ci_failure_analyzer.models.log_models import LogSegment, SegmentType


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class StageDefinition:
    """Definition of a pipeline stage."""
    name: str
    parents: List[str] = field(default_factory=list)  # Supports multiple parents
    agent_label: Optional[str] = None
    is_parallel_branch: bool = False
    is_parallel_container: bool = False
    has_nested_stages: bool = False
    entry_markers: List[str] = field(default_factory=list)


@dataclass
class PipelineModel:
    """Pipeline structure extracted from Jenkinsfile."""
    main_agent: str = ""
    sequential_phases: List[str] = field(default_factory=list)
    parallel_container: Optional[str] = None
    parallel_branches: Dict[str, str] = field(default_factory=dict)  # branch -> agent
    post_parallel_stages: List[str] = field(default_factory=list)
    stages: Dict[str, StageDefinition] = field(default_factory=dict)
    agent_locations: Dict[str, str] = field(default_factory=dict)
    dynamic_skip_stages: List[str] = field(default_factory=list)  # Stages with "Skipped " prefix
    custom_markers: Dict[str, str] = field(default_factory=dict)  # marker -> stage

    # NEW: Computed reverse mapping
    _agent_to_branch: Dict[str, str] = field(default_factory=dict, repr=False)
    
    def __post_init__(self):
        """Build reverse mapping after initialization."""
        self._build_agent_to_branch_map()
    
    def _build_agent_to_branch_map(self) -> None:
        """Build agent -> branch reverse lookup."""
        self._agent_to_branch = {}
        for branch, agent in self.parallel_branches.items():
            self._agent_to_branch[agent] = branch
            # Also map by lowercase for flexible matching
            self._agent_to_branch[agent.lower()] = branch
    
    def get_branch_for_agent(self, agent_node: str) -> Optional[str]:
        """Lookup branch name from agent node identifier."""
        if not agent_node:
            return None
            
        # Direct match
        if agent_node in self._agent_to_branch:
            return self._agent_to_branch[agent_node]
        
        # Case-insensitive exact match
        agent_lower = agent_node.lower()
        if agent_lower in self._agent_to_branch:
            return self._agent_to_branch[agent_lower]
        
        # Partial match: check if agent label is contained in node name
        for agent_label, branch in self._agent_to_branch.items():
            label_lower = agent_label.lower()
            # Match if label is substring of node name
            if label_lower in agent_lower:
                return branch
            # Match if node name is substring of label
            if agent_lower in label_lower:
                return branch
        
        return None
@dataclass
class ParserState:
    """Mutable parser state."""
    current_phase: Optional[str] = None
    current_stage: Optional[str] = None
    current_branch: Optional[str] = None
    current_agent: Optional[str] = None
    in_parallel: bool = False
    line_number: int = 0
    # NEW: Deduplication tracking
    recent_segments: Dict[str, int] = field(default_factory=dict)  # name -> line_number
    seen_branches: set = field(default_factory=set)
    
    def is_duplicate(self, name: str, window: int = 5) -> bool:
        """Check if segment was recently created (within N lines)."""
        if name in self.recent_segments:
            last_line = self.recent_segments[name]
            if self.line_number - last_line < window:
                return True
        return False
    
    def record_segment(self, name: str) -> None:
        """Record segment creation for deduplication."""
        self.recent_segments[name] = self.line_number

# =============================================================================
# Log Loader
# =============================================================================

class LogLoader:
    """Hierarchical Jenkins log loader and segmenter."""

    # FIXED: Correct regex patterns
    # Only match block entry, NOT stage declaration
    RE_STAGE_BLOCK = re.compile(r'^\[Pipeline\]\s+\{\s*\(([^)]+)\)\s*$')
    # Ignore stage declarations (don't create segments for these)
    RE_STAGE_DECL = re.compile(r'^\[Pipeline\]\s+stage\s*\(([^)]+)\)\s*$')
    # Fixed: removed erroneous backticks
    RE_PARALLEL_START = re.compile(r'^\[Pipeline\]\s+parallel\s*$')
    RE_PARALLEL_END = re.compile(r'^\[Pipeline\]\s+//\s*parallel\s*$')
    RE_BRANCH = re.compile(r'^\[Pipeline\]\s+\{\s*\(Branch:\s*([^)]+)\)\s*$')
    RE_RUNNING_ON = re.compile(r'^Running on\s+(\S+)\s+in\s+')
    RE_SKIPPED_WHEN = re.compile(r'^Stage\s+"([^"]+)"\s+skipped\s+due\s+to\s+when')


    def __init__(self, model: Optional[PipelineModel] = None):
        self.model = model or create_rfe_pipeline_model()

    def load(self, path: Union[str, Path]) -> List[LogSegment]:
        """Load and segment a Jenkins log file."""
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return self._process_stream(f)

    def load_from_string(self, content: str) -> List[LogSegment]:
        """Load and segment from string."""
        from io import StringIO
        return self._process_stream(StringIO(content))

    def _process_stream(self, stream: TextIO) -> List[LogSegment]:
        """Process log stream into segments."""
        segments: List[LogSegment] = []
        current_segment: Optional[LogSegment] = None
        buffer: List[str] = []
        state = ParserState()

        for line in self._read_lines(stream):
            state.line_number += 1
            transition = self._detect_transition(line, state)

            if transition:
                # Save previous segment
                if current_segment:
                    current_segment.content = buffer.copy()
                    current_segment.metadata['end_line'] = str(state.line_number - 1)
                    current_segment.metadata['line_count'] = str(len(buffer))
                    segments.append(current_segment)
                    buffer.clear()

                # Create new segment
                current_segment = self._create_segment(transition, state)

            buffer.append(line)

        # Save final segment
        if current_segment and buffer:
            current_segment.content = buffer
            current_segment.metadata['end_line'] = str(state.line_number)
            current_segment.metadata['line_count'] = str(len(buffer))
            segments.append(current_segment)

        return self._post_process(segments)

    def _read_lines(self, stream: TextIO) -> Generator[str, None, None]:
        """Yield lines from stream."""
        for line in stream:
            yield line.rstrip('\n\r')
    
    def _resolve_branch_for_stage(
        self, 
        stage_def: Optional[StageDefinition], 
        state: ParserState
    ) -> Optional[str]:
        """
        Resolve the correct branch for a stage based on model definition and agent context.
        
        Priority:
        1. Stage's parents list (if single parallel branch parent)
        2. Stage's agent_label (lookup branch from agent)
        3. Current agent (lookup branch from current agent)
        4. Fallback to state.current_branch
        """
        if not state.in_parallel:
            return None
        
        # Priority 1: Check stage definition's parents
        if stage_def and stage_def.parents:
            # Filter to only parallel branch parents
            parallel_parents = [
                p for p in stage_def.parents 
                if p in self.model.parallel_branches
            ]
            
            if len(parallel_parents) == 1:
                # Unambiguous: stage belongs to exactly one parallel branch
                return parallel_parents[0]
            
            if len(parallel_parents) > 1:
                # Multiple possible parents - use agent to disambiguate
                if state.current_agent:
                    agent_branch = self.model.get_branch_for_agent(state.current_agent)
                    if agent_branch in parallel_parents:
                        return agent_branch
                # Fallback: if current_branch is valid parent, use it
                if state.current_branch in parallel_parents:
                    return state.current_branch
                # Last resort: return first valid parent
                return parallel_parents[0]
        
        # Priority 2: Check stage definition's agent_label
        if stage_def and stage_def.agent_label:
            branch = self.model.get_branch_for_agent(stage_def.agent_label)
            if branch:
                return branch
        
        # Priority 3: Use current agent to lookup branch
        if state.current_agent:
            branch = self.model.get_branch_for_agent(state.current_agent)
            if branch:
                return branch
        
        # Priority 4: Fallback to current branch from state
        return state.current_branch
 
    

    def _detect_transition(self, line: str, state: ParserState) -> Optional[Dict]:
        """Detect segment transitions with deduplication and branch context."""
        
        # Skip stage declarations (these don't create segments)
        if self.RE_STAGE_DECL.match(line):
            return None
        
        # Parallel block boundaries
        if self.RE_PARALLEL_START.match(line):
            state.in_parallel = True
            if self.model.parallel_container:
                # Only create if not duplicate
                if state.is_duplicate(self.model.parallel_container):
                    return None
                state.record_segment(self.model.parallel_container)
                return {'type': 'parallel_container', 'name': self.model.parallel_container}
            return None

        if self.RE_PARALLEL_END.match(line):
            state.in_parallel = False
            state.current_branch = None
            state.current_agent = None
            return None

        # Agent detection - UPDATE branch context
        if match := self.RE_RUNNING_ON.match(line):
            node_name = match.group(1)
            state.current_agent = node_name
            # Lookup branch from agent
            branch = self.model.get_branch_for_agent(node_name)
            if branch and state.in_parallel:
                state.current_branch = branch
            return None  # Agent line doesn't create segment

        # Branch transitions
        if match := self.RE_BRANCH.match(line):
            branch = match.group(1)
            # Skip if already seen this branch
            if branch in state.seen_branches:
                return None
            if branch in self.model.parallel_branches:
                state.seen_branches.add(branch)
                state.current_branch = branch
                state.current_agent = self.model.parallel_branches[branch]
                state.record_segment(branch)
                return {
                    'type': 'branch', 
                    'name': branch, 
                    'agent': state.current_agent
                }
            return None

        # Stage block entry
        if match := self.RE_STAGE_BLOCK.match(line):
            stage_name = match.group(1)
            
            # Skip "Branch: X" patterns (handled above)
            if stage_name.startswith('Branch:'):
                return None
            
            # Normalize skipped stage names
            normalized = self._normalize_stage_name(stage_name)
            # Get stage definition from model
            stage_def = self._get_stage_def(normalized, state)
            
            # CRITICAL: Resolve correct branch using model + agent context
            branch_context = self._resolve_branch_for_stage(stage_def, state)
            
            # Update state with resolved branch (for subsequent stages)
            if branch_context and state.in_parallel:
                state.current_branch = branch_context
            
            # Deduplication key includes resolved branch
            dedup_key = f"{branch_context or 'main'}:{normalized}"
            if state.is_duplicate(dedup_key):
                return None
            state.record_segment(dedup_key)
            
            seg_type = self._determine_type(stage_def, state)
            state.current_stage = normalized
            
            return {
                'type': seg_type,
                'name': stage_name,
                'normalized': normalized,
                'definition': stage_def,
                'branch_context': branch_context,
                'agent_context': state.current_agent,
            }

        return None

    def _normalize_stage_name(self, name: str) -> str:
        """Normalize stage name, removing 'Skipped ' prefix if present."""
        for base_name in self.model.dynamic_skip_stages:
            if name == f"Skipped {base_name}":
                return base_name
        return name

    def _get_stage_def(self, name: str, state: ParserState) -> Optional[StageDefinition]:
        """Get stage definition, considering current branch context."""
        if name in self.model.stages:
            stage = self.model.stages[name]
            # If stage has multiple parents, verify context matches
            if stage.parents and state.current_branch:
                if state.current_branch in stage.parents:
                    return stage
            return stage
        return None

    def _determine_type(self, stage_def: Optional[StageDefinition], state: ParserState) -> str:
        """Determine segment type based on stage definition and context."""
        if not stage_def:
            return 'stage'
        if stage_def.is_parallel_container:
            return 'parallel_container'
        if stage_def.is_parallel_branch:
            return 'branch'
        if state.in_parallel:
            return 'nested_stage'
        return 'phase' if not stage_def.parents else 'stage'

    def _resolve_agent(self, node_name: str) -> str:
        """Resolve node name to agent label."""
        node_lower = node_name.lower()
        for label in list(self.model.parallel_branches.values()) + [self.model.main_agent]:
            if label.lower() in node_lower:
                return label
        return node_name
    def _create_segment(self, transition: Dict, state: ParserState) -> LogSegment:
        """Create LogSegment from transition info with proper branch context."""
        seg_type_map = {
            'phase': SegmentType.PHASE,
            'parallel_container': SegmentType.PHASE,
            'branch': SegmentType.BRANCH,
            'nested_stage': SegmentType.STAGE,
            'stage': SegmentType.STAGE,
        }

        name = transition['name']
        normalized = transition.get('normalized', name)
        trans_type = transition['type']

        # Build metadata
        metadata = {
            'start_line': str(state.line_number),
            'transition_type': trans_type,
        }

        # Add normalized name if different (for skipped stages)
        if normalized != name:
            metadata['normalized_name'] = normalized
            metadata['is_skipped'] = 'true'

        # FIXED: Always add branch context for nested stages
        branch_context = transition.get('branch_context') or state.current_branch
        if branch_context and trans_type not in ('branch', 'parallel_container', 'phase'):
            metadata['parent_branch'] = branch_context

        # Add agent information
        agent_context = transition.get('agent_context') or state.current_agent
        if agent_context:
            metadata['agent_label'] = agent_context
            if loc := self.model.agent_locations.get(agent_context):
                metadata['location'] = loc

        if state.in_parallel:
            metadata['in_parallel'] = 'true'

        # Update phase tracking
        if trans_type in ('phase', 'parallel_container'):
            state.current_phase = name

        return LogSegment(
            segment_type=seg_type_map.get(trans_type, SegmentType.STAGE),
            name=name,
            agent=agent_context,
            metadata=metadata,
        )
    def _post_process(self, segments: List[LogSegment]) -> List[LogSegment]:
        """Post-process: deduplicate, link hierarchy, group by branch."""
        if not segments:
            return segments

        # Step 1: Remove any remaining duplicates
        segments = self._deduplicate_segments(segments)
        
        # Step 2: Group segments by branch for proper ordering
        segments = self._reorder_by_branch(segments)
        
        # Step 3: Link hierarchy and set indices
        phase_idx = branch_idx = None
        for i, seg in enumerate(segments):
            seg.metadata['segment_index'] = str(i)

            if seg.segment_type == SegmentType.PHASE:
                phase_idx = i
                branch_idx = None
            elif seg.segment_type == SegmentType.BRANCH:
                if phase_idx is not None:
                    seg.metadata['parent_index'] = str(phase_idx)
                branch_idx = i
            elif seg.segment_type == SegmentType.STAGE:
                parent = branch_idx if branch_idx is not None else phase_idx
                if parent is not None:
                    seg.metadata['parent_index'] = str(parent)

            # Set execution status
            seg.metadata['status'] = 'skipped' if 'Skipped' in seg.name else 'executed'

        return segments
    
    def _deduplicate_segments(self, segments: List[LogSegment]) -> List[LogSegment]:
        """Remove duplicate segments based on (branch, name) key."""
        seen = set()
        result = []
        
        for seg in segments:
            # For branches, use name as key
            if seg.segment_type == SegmentType.BRANCH:
                key = ('_branch_', seg.name)
            # For phases, use name as key  
            elif seg.segment_type == SegmentType.PHASE:
                key = ('_phase_', seg.name)
            # For stages, include parent_branch in key
            else:
                branch = seg.metadata.get('parent_branch', '_no_branch_')
                key = (branch, seg.name, seg.segment_type.value)

            if key not in seen:
                seen.add(key)
                result.append(seg)

        return result
    def _reorder_by_branch(self, segments: List[LogSegment]) -> List[LogSegment]:
        """Reorder segments to group stages under their parent branches."""
        pre_parallel = []
        parallel_container = None
        branches: Dict[str, List[LogSegment]] = {}
        post_parallel = []
        
        parallel_seen = False
        parallel_ended = False

        for seg in segments:
            if seg.segment_type == SegmentType.PHASE:
                if seg.name == self.model.parallel_container:
                    parallel_container = seg
                    parallel_seen = True
                elif seg.name in self.model.post_parallel_stages:
                    post_parallel.append(seg)
                    parallel_ended = True
                elif parallel_ended:
                    post_parallel.append(seg)
                else:
                    pre_parallel.append(seg)

            elif seg.segment_type == SegmentType.BRANCH:
                branch_name = seg.name
                if branch_name not in branches:
                    branches[branch_name] = []
                branches[branch_name].append(seg)

            elif seg.segment_type == SegmentType.STAGE:
                # USE parent_branch FROM METADATA
                branch = seg.metadata.get('parent_branch')
                if branch and branch in self.model.parallel_branches:
                    if branch not in branches:
                        branches[branch] = []
                    branches[branch].append(seg)
                elif parallel_ended or not parallel_seen:
                    # Post-parallel or pre-parallel stage
                    if parallel_ended:
                        post_parallel.append(seg)
                    else:
                        pre_parallel.append(seg)
                else:
                    # Fallback: stage without branch context during parallel
                    # This shouldn't happen with proper branch tracking
                    post_parallel.append(seg)

        # Reassemble
        result = pre_parallel[:]

        if parallel_container:
            result.append(parallel_container)
            # Add branches in defined order from model
            for branch_name in self.model.parallel_branches.keys():
                if branch_name in branches:
                    result.extend(branches[branch_name])

        result.extend(post_parallel)

        return result
    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    def get_by_branch(self, segments: List[LogSegment], branch: str) -> List[LogSegment]:
        """Get all segments in a parallel branch."""
        result = []
        in_branch = False
        for seg in segments:
            if seg.segment_type == SegmentType.BRANCH:
                in_branch = (seg.name == branch)
            if in_branch:
                if seg.segment_type == SegmentType.PHASE:
                    break
                result.append(seg)
        return result

    def get_by_agent(self, segments: List[LogSegment], agent: str) -> List[LogSegment]:
        """Get segments that ran on specific agent."""
        return [s for s in segments if s.metadata.get('agent_label') == agent]

    def get_skipped(self, segments: List[LogSegment]) -> List[LogSegment]:
        """Get skipped segments."""
        return [s for s in segments if s.metadata.get('status') == 'skipped']

    def summary(self, segments: List[LogSegment]) -> Dict:
        """Generate pipeline execution summary."""
        return {
            'total_segments': len(segments),
            'phases': [s.name for s in segments if s.segment_type == SegmentType.PHASE],
            'branches': [s.name for s in segments if s.segment_type == SegmentType.BRANCH],
            'skipped': [s.name for s in segments if s.metadata.get('status') == 'skipped'],
            'agents': list({s.metadata.get('agent_label') for s in segments if s.metadata.get('agent_label')}),
        }


# =============================================================================
# RFE Pipeline Configuration
# =============================================================================

def create_rfe_pipeline_model() -> PipelineModel:
    """Create pipeline model for RFE validation Jenkinsfile."""
    
    model = PipelineModel(
        main_agent='STRX_Ubuntu20',
        
        sequential_phases=[
            'Declarative: Checkout SCM',
            'lfs pull',
            'Update Submodules',
            'copyrighter',
            'Build_rfeValidationApp',
            'Python_test_dryrun',
        ],
        
        parallel_container='RFEVal_EXECUTION',
        
        parallel_branches={
            'QTA_Test_Ubuntu-Python': 'STRX_Ubuntu20',
            'BLR-Tests': 'RFE_VAL_QTA_FSW',
            'BLR_QUAL_APP_Tests': 'RFE_VAL_QTA_FSW2',
            'EHV6-Python': 'RFE_VAL_QTA',
            'EHV5-Python': 'RFE_VAL_QTA2',
        },
        
        post_parallel_stages=[
            'Update_rfeSw',
            'Declarative: Post Actions' ,
            'Generate Test Report' ,
            ],  
        
        agent_locations={
            'STRX_Ubuntu20': 'Dresden (DRS)',
            'RFE_VAL_QTA_FSW': 'Bangalore (BLR) - NXW37473',
            'RFE_VAL_QTA_FSW2': 'Bangalore (BLR) - NXW37474',
            'RFE_VAL_QTA': 'Eindhoven (EHV) - Remote6',
            'RFE_VAL_QTA2': 'Eindhoven (EHV) - Remote5',
        },
        
        dynamic_skip_stages=[
            'RUN_QTA_NOISE_FLOOR_TEST_ON_ONECHIP',
            'RUN_QTA_FIT2_REG_CRC_TEST_ON_ONECHIP',
            'RUN_QTA_TEST_INTERFERENCE_RUN_ON_ONECHIP',
            'QTA tests on OneChip',
            'FuSaSysVal tests on OneChip/A53',
            'FuSaSysVal tests on OneChip/APPM7',
            'MATLAB tests on OneChip/SAF85XX',
            'Approve APPROVE test on OneChip/SAF85XX',
            'Approve QTA tests on OneChip/SAF85XX',
        ],
        
        custom_markers={
            'Starting QTA App A53 tests': 'RFE_QUAL_APP_ONECHIP_A53_NETBOOT',
            'Starting QTA App RSK tests': 'RFE_QUAL_APP_ONECHIP_RSK',
            'Run tests on OneChip/SAF85xx AppM7': 'RFE_QUAL_APP_OC_APPM7',
            'Run FuSa tests on OneChip': 'RUN_FUSASYSVAL_TEST',
            'Run MATLAB test on local setup': 'MATLAB_TEST_ON_DRS_NET_POOL',
            'Collecting the Results': 'RESULTS',
            'Collecting the results': 'RESULTS',
        },
    )

    # Ensure reverse mapping is built
    model._build_agent_to_branch_map()
    
    # Define stages - using composite approach for shared names
    stages = {
        # Sequential phases
        'lfs pull': StageDefinition(name='lfs pull', agent_label='STRX_Ubuntu20'),
        'Update Submodules': StageDefinition(name='Update Submodules', agent_label='STRX_Ubuntu20'),
        'copyrighter': StageDefinition(name='copyrighter', agent_label='STRX_Ubuntu20'),
        'Build_rfeValidationApp': StageDefinition(name='Build_rfeValidationApp', agent_label='STRX_Ubuntu20'),
        'Python_test_dryrun': StageDefinition(name='Python_test_dryrun', agent_label='STRX_Ubuntu20'),
        
        # Parallel container
        'RFEVal_EXECUTION': StageDefinition(
            name='RFEVal_EXECUTION',
            is_parallel_container=True,
        ),

        # Dynamic stages from helper functions (appear in multiple branches)
        'QTA tests on OneChip': StageDefinition(
            name='QTA tests on OneChip',
            parents=['QTA_Test_Ubuntu-Python', 'BLR_QUAL_APP_Tests'],
        ),
        'FuSaSysVal tests on OneChip/A53': StageDefinition(
            name='FuSaSysVal tests on OneChip/A53',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'MATLAB tests on OneChip/SAF85XX': StageDefinition(
            name='MATLAB tests on OneChip/SAF85XX',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'Approve APPROVE test on OneChip/SAF85XX': StageDefinition(
            name='Approve APPROVE test on OneChip/SAF85XX',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'Approve QTA tests on OneChip/SAF85XX': StageDefinition(
            name='Approve QTA tests on OneChip/SAF85XX',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'FuSaSysVal tests on OneChip/APPM7': StageDefinition(
            name='FuSaSysVal tests on OneChip/APPM7',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'Generate Test Report': StageDefinition(
            name='Generate Test Report',
            agent_label='STRX_Ubuntu20',
        ),

        
        # Parallel branches
        'QTA_Test_Ubuntu-Python': StageDefinition(
            name='QTA_Test_Ubuntu-Python',
            parents=['RFEVal_EXECUTION'],
            agent_label='STRX_Ubuntu20',
            is_parallel_branch=True,
            has_nested_stages=True,
        ),
        'BLR-Tests': StageDefinition(
            name='BLR-Tests',
            parents=['RFEVal_EXECUTION'],
            agent_label='RFE_VAL_QTA_FSW',
            is_parallel_branch=True,
            has_nested_stages=True,
        ),
        'BLR_QUAL_APP_Tests': StageDefinition(
            name='BLR_QUAL_APP_Tests',
            parents=['RFEVal_EXECUTION'],
            agent_label='RFE_VAL_QTA_FSW2',
            is_parallel_branch=True,
            has_nested_stages=True,
        ),
        'EHV6-Python': StageDefinition(
            name='EHV6-Python',
            parents=['RFEVal_EXECUTION'],
            agent_label='RFE_VAL_QTA',
            is_parallel_branch=True,
            has_nested_stages=True,
        ),
        'EHV5-Python': StageDefinition(
            name='EHV5-Python',
            parents=['RFEVal_EXECUTION'],
            agent_label='RFE_VAL_QTA2',
            is_parallel_branch=True,
            has_nested_stages=True,
        ),
        
        # Nested stages (shared names - use parents list)
        'Setup Environment': StageDefinition(
            name='Setup Environment',
            parents=['QTA_Test_Ubuntu-Python', 'BLR-Tests', 'BLR_QUAL_APP_Tests', 'EHV6-Python', 'EHV5-Python'],
        ),
        'Tests': StageDefinition(
            name='Tests',
            parents=['BLR-Tests', 'EHV6-Python', 'EHV5-Python'],
        ),
        'RESULTS': StageDefinition(
            name='RESULTS',
            parents=['QTA_Test_Ubuntu-Python', 'BLR-Tests', 'BLR_QUAL_APP_Tests', 'EHV6-Python', 'EHV5-Python'],
            entry_markers=['Collecting the Results', 'Collecting the results'],
        ),
        
        # QTA_Test_Ubuntu-Python nested stages
        'RFE_QUAL_APP_ONECHIP_A53_NETBOOT': StageDefinition(
            name='RFE_QUAL_APP_ONECHIP_A53_NETBOOT',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['Starting QTA App A53 tests for OneChip/SAF85xx'],
        ),
        'RFE_QUAL_APP_ONECHIP_RSK': StageDefinition(
            name='RFE_QUAL_APP_ONECHIP_RSK',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['Starting QTA App RSK tests for OneChip/SAF85xx'],
        ),
        'RFE_QUAL_APP_RFE_UNSUPPORTED_HW': StageDefinition(
            name='RFE_QUAL_APP_RFE_UNSUPPORTED_HW',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['RFE shall block unsupported HW.'],
        ),
        'RFE_QUAL_APP_OC_APPM7': StageDefinition(
            name='RFE_QUAL_APP_OC_APPM7',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['Run tests on OneChip/SAF85xx AppM7/CM7_0.'],
        ),
        'RUN_FUSASYSVAL_TEST_ON_ONECHIP_A53_NET_POOL': StageDefinition(
            name='RUN_FUSASYSVAL_TEST_ON_ONECHIP_A53_NET_POOL',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['Run FuSa tests on OneChip/SAF85xx A53.'],
        ),
        'RUN_FUSASYSVAL_TEST_ON_ONECHIP_APPM7_NET_POOL': StageDefinition(
            name='RUN_FUSASYSVAL_TEST_ON_ONECHIP_APPM7_NET_POOL',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
        ),
        'MATLAB_TEST_ON_DRS_NET_POOL': StageDefinition(
            name='MATLAB_TEST_ON_DRS_NET_POOL',
            parents=['QTA_Test_Ubuntu-Python'],
            agent_label='STRX_Ubuntu20',
            entry_markers=['Run MATLAB test on local setup.'],
        ),
        
        # BLR_QUAL_APP_Tests nested stages
        'RUN_QTA_TEST_ON_BLR_ONECHIP': StageDefinition(
            name='RUN_QTA_TEST_ON_BLR_ONECHIP',
            parents=['BLR_QUAL_APP_Tests'],
            agent_label='RFE_VAL_QTA_FSW2',
        ),
        'RUN_QTA_NOISE_FLOOR_TEST_ON_ONECHIP': StageDefinition(
            name='RUN_QTA_NOISE_FLOOR_TEST_ON_ONECHIP',
            parents=['BLR_QUAL_APP_Tests'],
            agent_label='RFE_VAL_QTA_FSW2',
            entry_markers=['Run QTA tests for RFE on OneChip/SAF85xx'],
        ),
        'RUN_QTA_FIT2_REG_CRC_TEST_ON_ONECHIP': StageDefinition(
            name='RUN_QTA_FIT2_REG_CRC_TEST_ON_ONECHIP',
            parents=['BLR_QUAL_APP_Tests'],
            agent_label='RFE_VAL_QTA_FSW2',
            entry_markers=['Run QTA tests for RFE on OneChip/SAF85xx ES2.3'],
        ),
        'RUN_QTA_TEST_INTERFERENCE_RUN_ON_ONECHIP': StageDefinition(
            name='RUN_QTA_TEST_INTERFERENCE_RUN_ON_ONECHIP',
            parents=['BLR_QUAL_APP_Tests'],
            agent_label='RFE_VAL_QTA_FSW2',
            entry_markers=['Run interference tests for RFE on OneChip/SAF85xx'],
        ),
        
        # Post-parallel stages
        'Update_rfeSw': StageDefinition(
            name='Update_rfeSw',
            agent_label='STRX_Ubuntu20',
        ),
    }
    
    model.stages = stages
    return model


# =============================================================================
# Convenience Functions
# =============================================================================

def load_log(path: Union[str, Path], model: Optional[PipelineModel] = None) -> List[LogSegment]:
    """Load and segment a Jenkins log file."""
    return LogLoader(model).load(path)


def load_log_string(content: str, model: Optional[PipelineModel] = None) -> List[LogSegment]:
    """Load and segment from string."""
    return LogLoader(model).load_from_string(content)

# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m ci_failure_analyzer.ingestion.log_loader <log_file>")
        print("       python -m ci_failure_analyzer.ingestion.log_loader raw_log.txt")
        sys.exit(1)
    
    log_path = sys.argv[1]
    
    try:
        segments = load_log(log_path)
        print(f"Found {len(segments)} segments:\n")
        
        for i, seg in enumerate(segments, 1):
            # Indent based on segment type
            if seg.segment_type == SegmentType.PHASE:
                indent = ""
            elif seg.segment_type == SegmentType.BRANCH:
                indent = "  "
            else:
                indent = "    "
            
            # Show branch context for stages
            branch_info = ""
            if seg.metadata.get('parent_branch'):
                branch_info = f" (branch: {seg.metadata['parent_branch']})"
            
            status = ""
            if seg.metadata.get('status') == 'skipped':
                status = " [SKIPPED]"
            
            print(f"{i:2}. {indent}[{seg.segment_type.value}] {seg.name}{branch_info}{status}")
        
        # Print summary
        loader = LogLoader()
        summary = loader.summary(segments)
        print(f"\n--- Summary ---")
        print(f"Total segments: {summary['total_segments']}")
        print(f"Phases: {len(summary['phases'])}")
        print(f"Branches: {len(summary['branches'])}")
        print(f"Skipped: {len(summary['skipped'])}")
        print(f"Agents: {summary['agents']}")
        
    except FileNotFoundError:
        print(f"Error: File not found: {log_path}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        raise

