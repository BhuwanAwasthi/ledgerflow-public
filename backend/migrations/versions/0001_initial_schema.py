"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('llm_cache',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('response', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('avatar_url', sa.String(length=1024), nullable=True),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('tour_state', sa.JSON(), nullable=False),
    sa.Column('preferences', sa.JSON(), nullable=False),
    sa.Column('baseline_version', sa.Integer(), nullable=True),
    sa.Column('case_seq', sa.Integer(), nullable=False),
    sa.Column('export_seq', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('config_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('author', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('config_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_config_versions_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_config_versions_path'), ['path'], unique=False)

    op.create_table('export_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=32), nullable=True),
    sa.Column('erp_profile', sa.String(length=64), nullable=False),
    sa.Column('case_ids', sa.JSON(), nullable=False),
    sa.Column('file_key', sa.String(length=512), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'ref')
    )
    with op.batch_alter_table('export_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_export_batches_owner_id'), ['owner_id'], unique=False)

    op.create_table('gl_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'code')
    )
    with op.batch_alter_table('gl_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gl_accounts_owner_id'), ['owner_id'], unique=False)

    op.create_table('vendors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('legal_name', sa.String(length=255), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=False),
    sa.Column('tax_id', sa.String(length=64), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=True),
    sa.Column('email_domain', sa.String(length=255), nullable=True),
    sa.Column('bank_last4', sa.String(length=4), nullable=True),
    sa.Column('iban_hash', sa.String(length=64), nullable=True),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('default_gl', sa.String(length=16), nullable=True),
    sa.Column('non_po_allowed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'code')
    )
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vendors_owner_id'), ['owner_id'], unique=False)

    op.create_table('purchase_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('po_number', sa.String(length=32), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_by', sa.String(length=128), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'po_number')
    )
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_purchase_orders_owner_id'), ['owner_id'], unique=False)

    op.create_table('cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=32), nullable=True),
    sa.Column('parent_case_id', sa.Integer(), nullable=True),
    sa.Column('submitter_name', sa.String(length=255), nullable=True),
    sa.Column('submitter_email', sa.String(length=255), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('current_stage', sa.String(length=64), nullable=True),
    sa.Column('decision', sa.String(length=32), nullable=True),
    sa.Column('decision_summary', sa.Text(), nullable=True),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('po_id', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('total', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('invoice_number', sa.String(length=64), nullable=True),
    sa.Column('invoice_number_norm', sa.String(length=64), nullable=True),
    sa.Column('invoice_date', sa.Date(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('config_snapshot', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sla_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('seed', sa.Boolean(), nullable=False),
    sa.Column('scenario_id', sa.String(length=64), nullable=True),
    sa.Column('approved_by', sa.String(length=128), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('exported_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'ref')
    )
    with op.batch_alter_table('cases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cases_invoice_number_norm'), ['invoice_number_norm'], unique=False)
        batch_op.create_index(batch_op.f('ix_cases_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_cases_scenario_id'), ['scenario_id'], unique=False)

    op.create_table('grns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('po_id', sa.Integer(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('qty_received', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('grns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_grns_owner_id'), ['owner_id'], unique=False)

    op.create_table('po_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('po_id', sa.Integer(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('gl_code', sa.String(length=16), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('po_lines', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_po_lines_owner_id'), ['owner_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('mime', sa.String(length=128), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('pages', sa.Integer(), nullable=True),
    sa.Column('label', sa.String(length=32), nullable=True),
    sa.Column('label_confidence', sa.Float(), nullable=True),
    sa.Column('page_from', sa.Integer(), nullable=True),
    sa.Column('page_to', sa.Integer(), nullable=True),
    sa.Column('has_text_layer', sa.Boolean(), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_owner_id'), ['owner_id'], unique=False)

    op.create_table('extractions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('field_meta', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('extractions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_extractions_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_extractions_owner_id'), ['owner_id'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('audience', sa.String(length=16), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_owner_id'), ['owner_id'], unique=False)

    op.create_table('po_amendments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('po_id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=True),
    sa.Column('amended_by', sa.String(length=128), nullable=False),
    sa.Column('old_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('new_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('po_amendments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_po_amendments_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_po_amendments_po_id'), ['po_id'], unique=False)

    op.create_table('po_consumption',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('po_id', sa.Integer(), nullable=False),
    sa.Column('po_line_id', sa.Integer(), nullable=True),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['po_line_id'], ['po_lines.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('po_consumption', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_po_consumption_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_po_consumption_po_id'), ['po_id'], unique=False)

    op.create_table('review_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('actor', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('changeset', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('review_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_review_actions_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_review_actions_owner_id'), ['owner_id'], unique=False)

    op.create_table('rule_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('run_no', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('rule_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rule_results_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rule_results_owner_id'), ['owner_id'], unique=False)

    op.create_table('stage_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('run_no', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('stage_id', sa.String(length=64), nullable=False),
    sa.Column('agent_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('outcome', sa.String(length=64), nullable=True),
    sa.Column('input_summary', sa.JSON(), nullable=False),
    sa.Column('output', sa.JSON(), nullable=False),
    sa.Column('reasoning', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'seq')
    )
    with op.batch_alter_table('stage_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stage_events_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_stage_events_owner_id'), ['owner_id'], unique=False)

    op.create_table('stream_tokens',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    with op.batch_alter_table('stream_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_stream_tokens_owner_id'), ['owner_id'], unique=False)

    op.create_table('template_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('erp_profile', sa.String(length=64), nullable=False),
    sa.Column('rows', sa.JSON(), nullable=False),
    sa.Column('file_key', sa.String(length=512), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('diff_from_prev', sa.JSON(), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('template_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_template_versions_case_id'), ['case_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_template_versions_owner_id'), ['owner_id'], unique=False)

    op.create_table('vendor_memory',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=False),
    sa.Column('hint', sa.Text(), nullable=False),
    sa.Column('source_case_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('vendor_memory', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vendor_memory_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_vendor_memory_vendor_id'), ['vendor_id'], unique=False)

    op.create_table('vendor_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('proposed_name', sa.String(length=255), nullable=True),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('docs_needed', sa.JSON(), nullable=False),
    sa.Column('docs_received', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('case_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('checks', sa.JSON(), nullable=False),
    sa.Column('message', sa.JSON(), nullable=True),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'token')
    )
    with op.batch_alter_table('vendor_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_vendor_requests_owner_id'), ['owner_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('vendor_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendor_requests_owner_id'))

    op.drop_table('vendor_requests')
    with op.batch_alter_table('vendor_memory', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendor_memory_vendor_id'))
        batch_op.drop_index(batch_op.f('ix_vendor_memory_owner_id'))

    op.drop_table('vendor_memory')
    with op.batch_alter_table('template_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_template_versions_owner_id'))
        batch_op.drop_index(batch_op.f('ix_template_versions_case_id'))

    op.drop_table('template_versions')
    with op.batch_alter_table('stream_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stream_tokens_owner_id'))

    op.drop_table('stream_tokens')
    with op.batch_alter_table('stage_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_stage_events_owner_id'))
        batch_op.drop_index(batch_op.f('ix_stage_events_case_id'))

    op.drop_table('stage_events')
    with op.batch_alter_table('rule_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rule_results_owner_id'))
        batch_op.drop_index(batch_op.f('ix_rule_results_case_id'))

    op.drop_table('rule_results')
    with op.batch_alter_table('review_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_review_actions_owner_id'))
        batch_op.drop_index(batch_op.f('ix_review_actions_case_id'))

    op.drop_table('review_actions')
    with op.batch_alter_table('po_consumption', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_po_consumption_po_id'))
        batch_op.drop_index(batch_op.f('ix_po_consumption_owner_id'))

    op.drop_table('po_consumption')
    with op.batch_alter_table('po_amendments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_po_amendments_po_id'))
        batch_op.drop_index(batch_op.f('ix_po_amendments_owner_id'))

    op.drop_table('po_amendments')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_owner_id'))

    op.drop_table('notifications')
    with op.batch_alter_table('extractions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_extractions_owner_id'))
        batch_op.drop_index(batch_op.f('ix_extractions_case_id'))

    op.drop_table('extractions')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_owner_id'))
        batch_op.drop_index(batch_op.f('ix_documents_case_id'))

    op.drop_table('documents')
    with op.batch_alter_table('po_lines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_po_lines_owner_id'))

    op.drop_table('po_lines')
    with op.batch_alter_table('grns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_grns_owner_id'))

    op.drop_table('grns')
    with op.batch_alter_table('cases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cases_scenario_id'))
        batch_op.drop_index(batch_op.f('ix_cases_owner_id'))
        batch_op.drop_index(batch_op.f('ix_cases_invoice_number_norm'))

    op.drop_table('cases')
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_orders_owner_id'))

    op.drop_table('purchase_orders')
    with op.batch_alter_table('vendors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_vendors_owner_id'))

    op.drop_table('vendors')
    with op.batch_alter_table('gl_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gl_accounts_owner_id'))

    op.drop_table('gl_accounts')
    with op.batch_alter_table('export_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_export_batches_owner_id'))

    op.drop_table('export_batches')
    with op.batch_alter_table('config_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_config_versions_path'))
        batch_op.drop_index(batch_op.f('ix_config_versions_owner_id'))

    op.drop_table('config_versions')
    op.drop_table('users')
    op.drop_table('llm_cache')
