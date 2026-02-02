"""
Review Schema Presets for KERBERUS Tabular Review.

Six specialized presets for different legal review scenarios:
1. Contract Review
2. Due Diligence
3. Employment Contracts
4. NDA Review
5. Court Case Summary
6. Document Discovery
"""

from typing import Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class FieldDefinition:
    """Definition of a single extraction field."""
    name: str
    display_name: str
    field_type: str  # string, date, boolean, enum, integer
    description: str
    enum_values: List[str] = field(default_factory=list)
    required: bool = True


@dataclass
class ReviewPreset:
    """Complete preset definition."""
    id: str
    name: str
    description: str
    icon: str
    fields: List[FieldDefinition]
    
    def get_field_names(self) -> List[str]:
        """Return list of field names."""
        return [f.name for f in self.fields]
    
    def get_display_names(self) -> List[str]:
        """Return list of display names for table headers."""
        return [f.display_name for f in self.fields]
    
    def to_prompt_schema(self) -> str:
        """Generate schema description for LLM prompt."""
        lines = []
        for f in self.fields:
            type_hint = f.field_type
            if f.field_type == "enum" and f.enum_values:
                type_hint = f"one of: {', '.join(f.enum_values)}"
            lines.append(f"- {f.name} ({type_hint}): {f.description}")
        return "\n".join(lines)


# =============================================================================
# PRESET 1: CONTRACT REVIEW
# =============================================================================
CONTRACT_REVIEW = ReviewPreset(
    id="contract_review",
    name="Contract Review",
    description="Analyze commercial agreements, service contracts, and legal documents",
    icon="📄",
    fields=[
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("contract_type", "Type", "string", "Service, License, Supply, Lease, etc."),
        FieldDefinition("party_a_name", "Party A", "string", "First party legal name"),
        FieldDefinition("party_a_jurisdiction", "Party A Jurisdiction", "string", "Country/Canton of incorporation"),
        FieldDefinition("party_b_name", "Party B", "string", "Second party legal name"),
        FieldDefinition("party_b_jurisdiction", "Party B Jurisdiction", "string", "Country/Canton of incorporation"),
        FieldDefinition("effective_date", "Effective Date", "date", "When contract becomes effective"),
        FieldDefinition("expiration_date", "Expiration Date", "date", "End date or 'Indefinite'"),
        FieldDefinition("contract_value", "Value", "string", "Total monetary value"),
        FieldDefinition("currency", "Currency", "string", "CHF, EUR, USD, etc."),
        FieldDefinition("payment_terms", "Payment Terms", "string", "Net 30, milestone, etc."),
        FieldDefinition("auto_renewal", "Auto-Renewal", "boolean", "Does it auto-renew?"),
        FieldDefinition("renewal_terms", "Renewal Terms", "string", "Renewal period and conditions"),
        FieldDefinition("termination_for_convenience", "Term. for Convenience", "boolean", "Can either party terminate without cause?"),
        FieldDefinition("termination_notice_period", "Notice Period", "string", "Days/months required notice"),
        FieldDefinition("termination_for_cause", "Term. for Cause", "string", "What constitutes cause for termination"),
        FieldDefinition("change_of_control_clause", "CoC Clause", "boolean", "Has Change of Control provision?"),
        FieldDefinition("change_of_control_details", "CoC Details", "string", "CoC trigger and consequences"),
        FieldDefinition("assignment_restrictions", "Assignment", "string", "Can the contract be assigned?"),
        FieldDefinition("liability_cap", "Liability Cap", "string", "Maximum liability amount"),
        FieldDefinition("indemnification", "Indemnification", "string", "Who indemnifies whom"),
        FieldDefinition("governing_law", "Governing Law", "string", "Which law governs"),
        FieldDefinition("dispute_resolution", "Dispute Resolution", "string", "Court, arbitration, mediation"),
        FieldDefinition("confidentiality_obligations", "Confidentiality", "string", "NDA provisions within contract"),
        FieldDefinition("ip_ownership", "IP Ownership", "string", "Who owns created IP"),
        FieldDefinition("key_deliverables", "Key Deliverables", "string", "Main obligations of each party"),
        FieldDefinition("penalties_liquidated_damages", "Penalties", "string", "Financial penalties for breach"),
        FieldDefinition("insurance_requirements", "Insurance", "string", "Required insurance coverage"),
        FieldDefinition("compliance_requirements", "Compliance", "string", "Regulatory/compliance obligations"),
        FieldDefinition("risk_level", "Risk Level", "enum", "Overall risk assessment", ["Low", "Medium", "High", "Critical"]),
        FieldDefinition("risk_factors", "Risk Factors", "string", "What drives the risk assessment"),
        FieldDefinition("special_terms", "Special Terms", "string", "Unusual or noteworthy provisions"),
    ]
)


# =============================================================================
# PRESET 2: DUE DILIGENCE
# =============================================================================
DUE_DILIGENCE = ReviewPreset(
    id="due_diligence",
    name="Due Diligence",
    description="M&A transaction review and corporate due diligence",
    icon="🔍",
    fields=[
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("document_type", "Doc Type", "string", "Corporate doc, contract, financial, regulatory"),
        FieldDefinition("entity_name", "Entity", "string", "Target company name"),
        FieldDefinition("entity_type", "Entity Type", "string", "AG, GmbH, SA, etc."),
        FieldDefinition("jurisdiction", "Jurisdiction", "string", "Country/Canton of registration"),
        FieldDefinition("registration_number", "Reg. Number", "string", "Commercial register number"),
        FieldDefinition("date_of_incorporation", "Incorporation Date", "date", "When entity was formed"),
        FieldDefinition("share_capital", "Share Capital", "string", "Authorized/issued capital"),
        FieldDefinition("shareholder_structure", "Shareholders", "string", "Major shareholders"),
        FieldDefinition("board_composition", "Board", "string", "Directors and officers"),
        FieldDefinition("material_contracts", "Material Contracts", "string", "Key contracts identified"),
        FieldDefinition("contract_assignability", "Assignability", "string", "Can contracts be assigned in transaction?"),
        FieldDefinition("change_of_control_triggers", "CoC Triggers", "string", "Contracts with CoC clauses"),
        FieldDefinition("pending_litigation", "Litigation", "string", "Current lawsuits"),
        FieldDefinition("regulatory_licenses", "Licenses", "string", "Required licenses and permits"),
        FieldDefinition("license_transferability", "License Transfer", "string", "Can licenses transfer?"),
        FieldDefinition("employment_issues", "Employment Issues", "string", "Key employment concerns"),
        FieldDefinition("pension_liabilities", "Pension", "string", "Pension fund status"),
        FieldDefinition("environmental_issues", "Environmental", "string", "Environmental liabilities"),
        FieldDefinition("ip_portfolio", "IP Portfolio", "string", "Patents, trademarks, copyrights"),
        FieldDefinition("ip_ownership_issues", "IP Issues", "string", "Any IP ownership disputes"),
        FieldDefinition("data_protection_compliance", "Data Protection", "string", "GDPR/FADP compliance status"),
        FieldDefinition("tax_issues", "Tax Issues", "string", "Outstanding tax matters"),
        FieldDefinition("real_estate", "Real Estate", "string", "Owned/leased properties"),
        FieldDefinition("red_flags", "Red Flags", "string", "Critical issues identified"),
        FieldDefinition("material_findings", "Material Findings", "string", "Key discoveries"),
        FieldDefinition("deal_breakers", "Deal Breakers", "string", "Issues that could kill the deal"),
        FieldDefinition("risk_level", "Risk Level", "enum", "Overall risk assessment", ["Low", "Medium", "High", "Critical"]),
        FieldDefinition("recommendation", "Recommendation", "enum", "Overall recommendation", ["Proceed", "Proceed with conditions", "Do not proceed"]),
        FieldDefinition("follow_up_required", "Follow-up", "string", "Items needing further investigation"),
    ]
)


# =============================================================================
# PRESET 3: EMPLOYMENT CONTRACTS
# =============================================================================
EMPLOYMENT_CONTRACTS = ReviewPreset(
    id="employment_contracts",
    name="Employment Contracts",
    description="Analyze employment agreements and HR contracts",
    icon="👔",
    fields=[
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("employee_name", "Employee", "string", "Full legal name"),
        FieldDefinition("employee_nationality", "Nationality", "string", "Citizenship"),
        FieldDefinition("work_permit_required", "Work Permit", "boolean", "Needs work authorization?"),
        FieldDefinition("employer_entity", "Employer", "string", "Employing company"),
        FieldDefinition("position_title", "Position", "string", "Job title"),
        FieldDefinition("department", "Department", "string", "Department/division"),
        FieldDefinition("reporting_to", "Reports To", "string", "Manager/supervisor"),
        FieldDefinition("employment_type", "Employment Type", "enum", "Type of employment", ["Permanent", "Fixed-term", "Temporary", "Part-time"]),
        FieldDefinition("start_date", "Start Date", "date", "Employment start"),
        FieldDefinition("end_date", "End Date", "date", "For fixed-term contracts"),
        FieldDefinition("probation_period", "Probation", "string", "Duration and terms"),
        FieldDefinition("base_salary", "Base Salary", "string", "Annual/monthly base"),
        FieldDefinition("currency", "Currency", "string", "CHF, EUR, etc."),
        FieldDefinition("bonus_structure", "Bonus", "string", "Variable compensation"),
        FieldDefinition("equity_grants", "Equity", "string", "Stock options, RSUs"),
        FieldDefinition("benefits", "Benefits", "string", "Insurance, pension, car, etc."),
        FieldDefinition("vacation_days", "Vacation", "string", "Annual leave entitlement"),
        FieldDefinition("working_hours", "Hours", "string", "Weekly hours, flexibility"),
        FieldDefinition("work_location", "Location", "string", "Office, remote, hybrid"),
        FieldDefinition("notice_period_employer", "Notice (Employer)", "string", "Employer's notice requirement"),
        FieldDefinition("notice_period_employee", "Notice (Employee)", "string", "Employee's notice requirement"),
        FieldDefinition("termination_restrictions", "Term. Restrictions", "string", "Protected periods, special conditions"),
        FieldDefinition("non_compete_clause", "Non-Compete", "boolean", "Has non-compete?"),
        FieldDefinition("non_compete_duration", "NC Duration", "string", "How long after termination"),
        FieldDefinition("non_compete_scope", "NC Scope", "string", "Geographic/industry scope"),
        FieldDefinition("non_compete_compensation", "NC Compensation", "string", "Payment during restriction"),
        FieldDefinition("non_solicitation", "Non-Solicitation", "string", "Client/employee non-solicit"),
        FieldDefinition("confidentiality", "Confidentiality", "string", "NDA provisions"),
        FieldDefinition("ip_assignment", "IP Assignment", "string", "Work product ownership"),
        FieldDefinition("garden_leave", "Garden Leave", "string", "Can employer place on garden leave?"),
        FieldDefinition("severance", "Severance", "string", "Termination payment provisions"),
        FieldDefinition("governing_law", "Governing Law", "string", "Applicable employment law"),
        FieldDefinition("risk_level", "Risk Level", "enum", "Overall risk assessment", ["Low", "Medium", "High"]),
        FieldDefinition("key_issues", "Key Issues", "string", "Notable concerns"),
    ]
)


# =============================================================================
# PRESET 4: NDA REVIEW
# =============================================================================
NDA_REVIEW = ReviewPreset(
    id="nda_review",
    name="NDA Review",
    description="Analyze confidentiality and non-disclosure agreements",
    icon="🔒",
    fields=[
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("nda_type", "NDA Type", "enum", "Type of NDA", ["Mutual", "One-way (Discloser)", "One-way (Recipient)"]),
        FieldDefinition("disclosing_party", "Discloser", "string", "Who shares confidential info"),
        FieldDefinition("receiving_party", "Recipient", "string", "Who receives confidential info"),
        FieldDefinition("effective_date", "Effective Date", "date", "When NDA starts"),
        FieldDefinition("term", "Term", "string", "Duration of agreement"),
        FieldDefinition("confidentiality_period", "Conf. Period", "string", "How long info stays confidential"),
        FieldDefinition("definition_of_confidential", "CI Definition", "string", "What's covered as confidential"),
        FieldDefinition("exclusions", "Exclusions", "string", "What's NOT confidential"),
        FieldDefinition("permitted_disclosures", "Permitted Disclosures", "string", "Who can recipient share with"),
        FieldDefinition("permitted_purposes", "Permitted Purposes", "string", "What can info be used for"),
        FieldDefinition("return_destruction", "Return/Destroy", "string", "What happens to info at end"),
        FieldDefinition("residuals_clause", "Residuals", "boolean", "Can keep info in unaided memory?"),
        FieldDefinition("reverse_engineering", "Reverse Engineering", "string", "Is reverse engineering allowed?"),
        FieldDefinition("injunctive_relief", "Injunctive Relief", "boolean", "Can seek injunction for breach?"),
        FieldDefinition("indemnification", "Indemnification", "string", "Indemnity provisions"),
        FieldDefinition("liability_cap", "Liability Cap", "string", "Maximum damages"),
        FieldDefinition("governing_law", "Governing Law", "string", "Which law applies"),
        FieldDefinition("dispute_resolution", "Dispute Resolution", "string", "Court/arbitration venue"),
        FieldDefinition("breach_notification", "Breach Notice", "string", "Notice requirements for breach"),
        FieldDefinition("surviving_obligations", "Surviving Obligations", "string", "What survives termination"),
        FieldDefinition("assignability", "Assignability", "string", "Can NDA be assigned?"),
        FieldDefinition("amendment_requirements", "Amendments", "string", "How to modify the NDA"),
        FieldDefinition("risk_level", "Risk Level", "enum", "Overall risk assessment", ["Low", "Medium", "High"]),
        FieldDefinition("concerns", "Concerns", "string", "Problematic provisions"),
    ]
)


# =============================================================================
# PRESET 5: COURT CASE SUMMARY
# =============================================================================
COURT_CASE_SUMMARY = ReviewPreset(
    id="court_case_summary",
    name="Court Case Summary",
    description="Analyze and summarize court decisions for case research",
    icon="⚖️",
    fields=[
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("case_number", "Case Number", "string", "Official case reference"),
        FieldDefinition("court", "Court", "string", "Which court (BGer, BVGer, cantonal)"),
        FieldDefinition("chamber", "Chamber", "string", "Division/chamber"),
        FieldDefinition("judges", "Judges", "string", "Panel composition"),
        FieldDefinition("decision_date", "Decision Date", "date", "Date of judgment"),
        FieldDefinition("plaintiff", "Plaintiff", "string", "Claimant/appellant"),
        FieldDefinition("plaintiff_counsel", "Plaintiff Counsel", "string", "Plaintiff's attorneys"),
        FieldDefinition("defendant", "Defendant", "string", "Respondent"),
        FieldDefinition("defendant_counsel", "Defendant Counsel", "string", "Defendant's attorneys"),
        FieldDefinition("lower_court", "Lower Court", "string", "Court below (if appeal)"),
        FieldDefinition("lower_court_outcome", "Lower Court Outcome", "string", "What lower court decided"),
        FieldDefinition("subject_matter", "Subject Matter", "string", "Legal area (contract, tort, etc.)"),
        FieldDefinition("legal_issues", "Legal Issues", "string", "Questions of law addressed"),
        FieldDefinition("factual_background", "Facts", "string", "Key facts"),
        FieldDefinition("plaintiff_claims", "Claims", "string", "What the plaintiff sought"),
        FieldDefinition("plaintiff_arguments", "Plaintiff Arguments", "string", "Key legal arguments"),
        FieldDefinition("defendant_defenses", "Defendant Arguments", "string", "Defendant's arguments"),
        FieldDefinition("outcome", "Outcome", "string", "Who won, relief granted"),
        FieldDefinition("damages_awarded", "Damages", "string", "Monetary judgment if any"),
        FieldDefinition("costs_decision", "Costs", "string", "Who pays court costs"),
        FieldDefinition("legal_principles", "Legal Principles", "string", "Rules of law established"),
        FieldDefinition("key_citations", "Key Citations", "string", "Important cases cited"),
        FieldDefinition("legislation_applied", "Legislation", "string", "Statutes interpreted"),
        FieldDefinition("precedent_value", "Precedent Value", "enum", "Importance of decision", ["Landmark", "Leading", "Important", "Routine"]),
        FieldDefinition("distinguishing_factors", "Distinguishing Factors", "string", "What makes this case unique"),
        FieldDefinition("practical_implications", "Implications", "string", "Impact on similar cases"),
        FieldDefinition("relevance_score", "Relevance", "enum", "Relevance to your matter", ["Highly Relevant", "Relevant", "Marginally Relevant"]),
        FieldDefinition("summary", "Summary", "string", "One-paragraph summary"),
    ]
)


# =============================================================================
# PRESET 6: DOCUMENT DISCOVERY
# =============================================================================
DOCUMENT_DISCOVERY = ReviewPreset(
    id="document_discovery",
    name="Document Discovery",
    description="E-discovery and document production review for litigation",
    icon="📁",
    fields=[
        FieldDefinition("document_id", "Doc ID", "string", "Internal Bates number or reference"),
        FieldDefinition("document_name", "Document", "string", "Original filename"),
        FieldDefinition("document_type", "Type", "string", "Email, Memo, Letter, Report, Contract, etc."),
        FieldDefinition("document_date", "Date", "date", "Date on document"),
        FieldDefinition("date_received", "Received", "date", "When document was received/collected"),
        FieldDefinition("custodian", "Custodian", "string", "Person who held the document"),
        FieldDefinition("author", "Author", "string", "Who created it"),
        FieldDefinition("recipients", "Recipients", "string", "To/CC recipients"),
        FieldDefinition("subject", "Subject", "string", "Title or subject line"),
        FieldDefinition("file_type", "File Type", "string", "PDF, DOCX, Email (.msg), etc."),
        FieldDefinition("page_count", "Pages", "integer", "Number of pages"),
        FieldDefinition("attachments", "Attachments", "string", "List of attachments"),
        FieldDefinition("source_location", "Source", "string", "Where document was found"),
        FieldDefinition("language", "Language", "string", "Document language"),
        FieldDefinition("relevance", "Relevance", "enum", "Relevance to case", ["Hot", "Relevant", "Potentially Relevant", "Not Relevant"]),
        FieldDefinition("issue_tags", "Issues", "string", "Which case issues it relates to"),
        FieldDefinition("key_content_summary", "Key Content", "string", "What the document says"),
        FieldDefinition("key_persons_mentioned", "Key Persons", "string", "People referenced"),
        FieldDefinition("key_dates_mentioned", "Key Dates", "string", "Important dates in content"),
        FieldDefinition("privilege_status", "Privilege", "enum", "Privilege assessment", ["Privileged", "Work Product", "Not Privileged", "Partial"]),
        FieldDefinition("privilege_type", "Privilege Type", "string", "Attorney-client, Litigation, Common Interest"),
        FieldDefinition("privilege_holder", "Privilege Holder", "string", "Who holds the privilege"),
        FieldDefinition("privilege_log_entry", "Privilege Log", "string", "Entry for privilege log"),
        FieldDefinition("redaction_required", "Redaction", "boolean", "Needs redaction?"),
        FieldDefinition("redaction_reason", "Redaction Reason", "string", "Why redaction is needed"),
        FieldDefinition("production_status", "Production", "enum", "Production decision", ["Produce", "Withhold", "Redact", "Pending Review"]),
        FieldDefinition("production_set", "Production Set", "string", "Which production batch"),
        FieldDefinition("reviewer", "Reviewer", "string", "Who reviewed the document"),
        FieldDefinition("review_date", "Review Date", "date", "When reviewed"),
        FieldDefinition("review_notes", "Notes", "string", "Reviewer's comments"),
        FieldDefinition("follow_up_required", "Follow-up", "boolean", "Needs further action?"),
        FieldDefinition("priority", "Priority", "enum", "Review priority", ["High", "Medium", "Low"]),
    ]
)


# =============================================================================
# PRESET REGISTRY
# =============================================================================
REVIEW_PRESETS: Dict[str, ReviewPreset] = {
    "contract_review": CONTRACT_REVIEW,
    "due_diligence": DUE_DILIGENCE,
    "employment_contracts": EMPLOYMENT_CONTRACTS,
    "nda_review": NDA_REVIEW,
    "court_case_summary": COURT_CASE_SUMMARY,
    "document_discovery": DOCUMENT_DISCOVERY,
}


def get_preset(preset_id: str) -> ReviewPreset:
    """Get a preset by ID."""
    if preset_id not in REVIEW_PRESETS:
        raise ValueError(f"Unknown preset: {preset_id}. Available: {list(REVIEW_PRESETS.keys())}")
    return REVIEW_PRESETS[preset_id]


def list_presets() -> List[Dict[str, str]]:
    """List all available presets with metadata."""
    return [
        {
            "id": preset.id,
            "name": preset.name,
            "icon": preset.icon,
            "description": preset.description,
            "field_count": len(preset.fields),
        }
        for preset in REVIEW_PRESETS.values()
    ]
