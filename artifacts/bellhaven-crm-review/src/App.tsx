import { type ReactNode, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import {
  Activity, ArrowDownLeft, ArrowRight, ArrowUpRight, BadgeCheck, Building2,
  Check, ChevronDown, CircleAlert, Clock3, ExternalLink, FileSearch, Filter,
  History, LoaderCircle, MapPin, Menu, RefreshCw, Search, ShieldCheck, X,
} from 'lucide-react';
import {
  getGetBellhavenReviewHealthQueryKey,
  getGetBellhavenReviewSummaryQueryKey,
  getListBellhavenCommunitiesQueryKey,
  getListBellhavenProposalsQueryKey,
  getListBellhavenReviewActivityQueryKey,
  useDecideBellhavenProposal,
  useGetBellhavenReviewHealth,
  useGetBellhavenReviewSummary,
  useListBellhavenCommunities,
  useListBellhavenProposals,
  useListBellhavenReviewActivity,
  useRunBellhavenReconciliation,
} from '@workspace/api-client-react';
import type { Community, Proposal } from '@workspace/api-client-react';
import { Link, Route, Router as WouterRouter, Switch, useLocation } from 'wouter';
import NotFound from '@/pages/not-found';

const queryClient = new QueryClient();
type NavItem = { href: string; label: string; icon: typeof Building2 };

function Shell({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const nav: NavItem[] = [
    { href: '/', label: 'Review queue', icon: FileSearch },
    { href: '/communities', label: 'Communities', icon: Building2 },
    { href: '/activity', label: 'Decision log', icon: History },
  ];
  return <div className="app-shell flex bg-background">
    <aside className="hidden md:flex md:w-[246px] shrink-0 flex-col bg-[hsl(var(--sidebar))] text-[hsl(var(--sidebar-foreground))] px-5 py-6">
      <Link href="/" className="mb-12 flex items-center gap-3 focus-ring rounded-md" data-testid="link-brand">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-[hsl(var(--sidebar-primary))] text-[hsl(var(--sidebar-primary-foreground))]"><Building2 size={20}/></span>
        <span><span className="block font-editorial text-[21px] leading-5">Bellhaven</span><span className="font-data text-[9px] uppercase tracking-[.17em] opacity-70">CRM review</span></span>
      </Link>
      <div className="mb-3 px-3 font-data text-[9px] uppercase tracking-[.18em] opacity-50">Workspace</div>
      <nav className="space-y-1">
        {nav.map(({ href, label, icon: Icon }) => <Link key={href} href={href} data-testid={`link-nav-${label.toLowerCase().replaceAll(' ', '-')}`} className={`flex min-h-12 items-center gap-3 rounded-lg px-3 text-[13px] transition-colors focus-ring ${location === href ? 'bg-[hsl(var(--sidebar-accent))] text-[hsl(var(--sidebar-accent-foreground))]' : 'opacity-75 hover:bg-[hsl(var(--sidebar-accent))] hover:opacity-100'}`}><Icon size={17}/>{label}{location === href && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[hsl(var(--sidebar-primary))]"/>}</Link>)}
      </nav>
      <div className="mt-auto rounded-xl border border-[hsl(var(--sidebar-border))] bg-white/[.035] p-4">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold"><ShieldCheck size={15} className="text-[hsl(var(--sidebar-primary))]"/>Controlled review</div>
        <p className="m-0 text-[11px] leading-[1.6] opacity-65">Changes are staged for review. Nothing reaches the CRM without an explicit decision.</p>
      </div>
      <div className="mt-5 flex items-center gap-2 px-2 text-[10px] opacity-50"><span className="h-1.5 w-1.5 rounded-full bg-[hsl(var(--sidebar-primary))]"/> Bellhaven operations</div>
    </aside>
    <div className="min-w-0 flex-1">
      <header className="sticky top-0 z-20 flex min-h-[62px] items-center justify-between border-b border-border bg-background/95 px-4 backdrop-blur md:px-8">
        <button type="button" aria-label="Toggle navigation" data-testid="button-toggle-navigation" className="focus-ring mr-2 grid h-11 w-11 place-items-center rounded-lg md:hidden" onClick={() => setMenuOpen(!menuOpen)}><Menu size={20}/></button>
        <div className="flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground"><span className="hidden sm:inline">Operations</span><span className="hidden sm:inline">/</span><span className="truncate font-medium text-foreground">{nav.find(n => n.href === location)?.label ?? 'Review'}</span></div>
        <div className="flex items-center gap-3"><span className="hidden items-center gap-2 rounded-full border border-border px-3 py-1.5 text-[10px] text-muted-foreground sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-primary"/>Manual decisions only</span><div className="grid h-8 w-8 place-items-center rounded-full bg-secondary font-data text-[10px] text-foreground">BH</div></div>
      </header>
      {menuOpen && <nav className="absolute z-30 w-full border-b border-border bg-card p-3 shadow-lg md:hidden">{nav.map(({href,label,icon:Icon})=><Link key={href} href={href} onClick={()=>setMenuOpen(false)} className="flex min-h-12 items-center gap-3 rounded-md px-3 text-sm hover:bg-secondary"><Icon size={17}/>{label}</Link>)}</nav>}
      <main className="mx-auto max-w-[1600px] px-4 py-6 md:px-8 md:py-8">{children}</main>
    </div>
  </div>;
}

function LoadingRows() { return <div className="space-y-3 p-4" aria-label="Loading"><div className="h-10 animate-pulse rounded bg-muted"/><div className="h-16 animate-pulse rounded bg-muted"/><div className="h-16 animate-pulse rounded bg-muted"/></div>; }
function ErrorNotice({ message, retry }: { message: string; retry: () => void }) { return <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/5 p-5 text-sm"><div className="flex items-center gap-2 font-semibold text-destructive"><CircleAlert size={17}/>{message}</div><button onClick={retry} className="focus-ring min-h-11 rounded-lg border border-border px-4 text-xs font-semibold hover:bg-secondary" data-testid="button-retry">Retry connection</button></div>; }
function fmtDate(value?: string | null) { if (!value) return 'Not yet run'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }); }
function money(value?: number | null) { return value == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value); }
function Kpi({ label, value, note, icon: Icon, tone = 'default' }: {label:string;value:string|number;note:string;icon:typeof Building2;tone?:string}) {
  return <div className="rounded-xl border border-border bg-card p-4 md:p-5"><div className="flex items-start justify-between"><span className="font-data text-[9px] uppercase tracking-[.15em] text-muted-foreground">{label}</span><span className={`grid h-8 w-8 place-items-center rounded-lg ${tone}`}><Icon size={16}/></span></div><div className="mt-4 font-editorial text-3xl">{value}</div><div className="mt-1 text-[11px] text-muted-foreground">{note}</div></div>;
}
function StatusPill({ status }: { status: string }) {
  const styles: Record<string,string> = { pending:'bg-[hsl(39_72%_89%)] text-[hsl(28_48%_34%)]', approved:'bg-[hsl(155_33%_87%)] text-[hsl(165_38%_27%)]', rejected:'bg-[hsl(5_48%_92%)] text-[hsl(3_48%_40%)]', high:'bg-[hsl(5_48%_92%)] text-[hsl(3_48%_40%)]', medium:'bg-[hsl(39_72%_89%)] text-[hsl(28_48%_34%)]', low:'bg-[hsl(155_33%_87%)] text-[hsl(165_38%_27%)]' };
  return <span className={`inline-flex min-h-6 items-center rounded-full px-2.5 font-data text-[9px] uppercase tracking-[.09em] ${styles[status.toLowerCase()] ?? 'bg-secondary text-muted-foreground'}`}>{status.replaceAll('_',' ')}</span>;
}
function Header({ eyebrow, title, description, action }: {eyebrow:string;title:string;description:string;action?:ReactNode}) {
  return <div className="mb-7 flex flex-col justify-between gap-4 md:mb-9 md:flex-row md:items-end"><div><div className="mb-2 font-data text-[9px] uppercase tracking-[.19em] text-primary">{eyebrow}</div><h1 className="m-0 font-editorial text-[34px] leading-[1.06] tracking-[-.025em] md:text-[43px]">{title}</h1><p className="mb-0 mt-2 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">{description}</p></div>{action}</div>;
}

function SystemStatus() {
  const health = useGetBellhavenReviewHealth();
  if (health.isLoading) return <div className="h-10 animate-pulse rounded-xl bg-muted"/>;
  if (health.isError) return <div className="rounded-xl border border-border px-4 py-3 text-xs text-muted-foreground">Service readiness unavailable. Review actions may not be available.</div>;
  const ready = health.data;
  const needsToken = !ready?.crm_configured;
  return <div className={`flex flex-col gap-3 rounded-xl border px-4 py-3 md:flex-row md:items-center md:justify-between ${needsToken ? 'border-[hsl(18_43%_53%/.4)] bg-[hsl(18_43%_53%/.07)]' : 'border-primary/20 bg-primary/[.045]'}`}>
    <div className="flex items-start gap-3"><span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${needsToken?'bg-[hsl(18_43%_53%/.15)] text-accent':'bg-primary/10 text-primary'}`}>{needsToken?<CircleAlert size={17}/>:<ShieldCheck size={17}/>}</span><div><div className="text-[12px] font-semibold">{needsToken ? 'CRM connection not configured' : 'Review service ready'}</div><div className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{needsToken ? 'Missing CRM token. Source inventory and staged proposals remain viewable; decisions require CRM configuration.' : `Read-only review mode is ${ready?.read_only ? 'on' : 'off'}. Explicit approval or rejection is required for each proposal.`}</div></div></div>
    <div className="flex flex-wrap gap-2 pl-11 md:pl-0"><span className="rounded-md bg-card px-2.5 py-1.5 font-data text-[9px] text-muted-foreground">Website {ready?.website_available?'available':'unavailable'}</span><span className="rounded-md bg-card px-2.5 py-1.5 font-data text-[9px] text-muted-foreground">CRM {ready?.crm_configured?'configured':'not configured'}</span><span className="rounded-md bg-card px-2.5 py-1.5 font-data text-[9px] text-muted-foreground">Read only {ready?.read_only?'yes':'no'}</span></div>
  </div>;
}

function Dashboard() {
  const [status, setStatus] = useState('pending');
  const [search, setSearch] = useState('');
  const [submittedSearch, setSubmittedSearch] = useState('');
  const [selected, setSelected] = useState<Proposal | null>(null);
  const qc = useQueryClient();
  const summary = useGetBellhavenReviewSummary();
  const proposals = useListBellhavenProposals({ status: status === 'all' ? undefined : status as 'pending'|'approved'|'rejected', search: submittedSearch || undefined, page: 1, page_size: 50 });
  const decision = useDecideBellhavenProposal();
  const run = useRunBellhavenReconciliation();
  const [reviewerNotes, setReviewerNotes] = useState('');
  const [decisionMessage, setDecisionMessage] = useState('');
  const refresh = () => { void qc.invalidateQueries({ queryKey: getGetBellhavenReviewSummaryQueryKey() }); void qc.invalidateQueries({ queryKey: getListBellhavenProposalsQueryKey() }); void qc.invalidateQueries({ queryKey: getGetBellhavenReviewHealthQueryKey() }); };
  const selectedProposal = selected ? (proposals.data?.items.find(p => p.id === selected.id) ?? selected) : null;
  const makeDecision = (value:'approved'|'rejected') => {
    if (!selectedProposal) return;
    setDecisionMessage('');
    decision.mutate({ proposalId: selectedProposal.id, data: { decision: value, ...(reviewerNotes.trim() ? { reviewer_notes: reviewerNotes.trim() } : {}) } }, {
      onSuccess: (result) => {
        setDecisionMessage(result.message || `Proposal ${value}.`);
        setSelected(null); setReviewerNotes('');
        void qc.invalidateQueries({ queryKey: getListBellhavenProposalsQueryKey() });
        void qc.invalidateQueries({ queryKey: getGetBellhavenReviewSummaryQueryKey() });
        void qc.invalidateQueries({ queryKey: getListBellhavenReviewActivityQueryKey() });
      },
      onError: () => setDecisionMessage('Decision could not be saved. Check CRM configuration and retry.'),
    });
  };
  const startRun = () => run.mutate(undefined, { onSuccess: refresh });
  const data = summary.data;
  return <div className="fade-in space-y-6">
    <Header eyebrow="Reconciliation / workspace" title="Review, then reconcile." description="Compare Bellhaven’s source locations with CRM account snapshots. Every proposed change is visible before you decide." action={<div className="flex gap-2"><button onClick={refresh} className="focus-ring flex min-h-11 items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-semibold hover:bg-secondary" data-testid="button-refresh"><RefreshCw size={15}/>Refresh</button><button onClick={startRun} disabled={run.isPending} className="focus-ring flex min-h-11 items-center gap-2 rounded-lg bg-primary px-4 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60" data-testid="button-run-reconciliation">{run.isPending?<LoaderCircle className="animate-spin" size={15}/>:<Activity size={15}/>} {run.isPending?'Scanning…':'Run reconciliation'}</button></div>}/>
    <SystemStatus/>
    {run.data && <div className="rounded-xl border border-primary/20 bg-primary/[.045] p-4 text-[12px]"><div className="font-semibold">Reconciliation complete</div><div className="mt-1 text-muted-foreground">{run.data.communities_scanned} communities scanned · {run.data.accounts_scanned} accounts scanned · {run.data.proposals_created} new proposals · {run.data.proposals_existing} already staged</div>{run.data.warnings.length>0&&<div className="mt-2 text-accent">{run.data.warnings.join(' · ')}</div>}</div>}
    {run.isError && <ErrorNotice message="Reconciliation could not run. Confirm website availability and CRM setup." retry={startRun}/>}
    {decisionMessage && <div role="status" className="flex items-center justify-between rounded-lg border border-border bg-card px-4 py-3 text-xs"><span>{decisionMessage}</span><button aria-label="Dismiss notice" className="focus-ring grid h-8 w-8 place-items-center rounded" onClick={()=>setDecisionMessage('')}><X size={15}/></button></div>}
    {summary.isError ? <ErrorNotice message="Review summary is unavailable." retry={()=>void summary.refetch()}/> :
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Locations" value={summary.isLoading?'—':data?.total_communities??'—'} note="Source website inventory" icon={Building2} tone="bg-primary/10 text-primary"/>
        <Kpi label="CRM accounts" value={summary.isLoading?'—':data?.total_accounts??'—'} note={data?.crm_configured?'Snapshot connected':'Connection not configured'} icon={Activity} tone="bg-secondary text-foreground"/>
        <Kpi label="Awaiting review" value={summary.isLoading?'—':data?.pending_count??'—'} note="Staged, no CRM writes" icon={Clock3} tone="bg-[hsl(39_72%_89%)] text-[hsl(28_48%_34%)]"/>
        <Kpi label="High risk" value={summary.isLoading?'—':data?.high_risk_count??'—'} note="Manual attention recommended" icon={CircleAlert} tone="bg-[hsl(5_48%_92%)] text-[hsl(3_48%_40%)]"/>
      </div>}
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex flex-col gap-4 border-b border-border p-4 md:flex-row md:items-center md:justify-between md:px-5">
        <div><h2 className="m-0 font-editorial text-[23px]">Reconciliation proposals</h2><p className="mb-0 mt-1 text-[11px] text-muted-foreground">{proposals.data?.total ?? '—'} records · select a proposal to review its evidence</p></div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <form className="relative" onSubmit={e=>{e.preventDefault();setSubmittedSearch(search.trim());}}><Search size={15} className="absolute left-3 top-3.5 text-muted-foreground"/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search proposals" aria-label="Search proposals" data-testid="input-proposal-search" className="focus-ring h-11 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-xs outline-none sm:w-52"/></form>
          <label className="relative"><Filter size={14} className="absolute left-3 top-3.5 pointer-events-none text-muted-foreground"/><select value={status} onChange={e=>{setStatus(e.target.value);setSelected(null);}} aria-label="Filter by status" data-testid="select-proposal-status" className="focus-ring h-11 w-full appearance-none rounded-lg border border-border bg-background pl-9 pr-9 text-xs sm:w-40"><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="all">All statuses</option></select><ChevronDown size={13} className="absolute right-3 top-4 pointer-events-none text-muted-foreground"/></label>
        </div>
      </div>
      <div className="grid min-h-[430px] lg:grid-cols-[minmax(0,1fr)_minmax(310px,410px)]">
        <div className="min-w-0">
          {proposals.isLoading ? <LoadingRows/> : proposals.isError ? <div className="p-4"><ErrorNotice message="Proposals could not be loaded." retry={()=>void proposals.refetch()}/></div> :
          !proposals.data?.items.length ? <div className="flex min-h-[300px] flex-col items-center justify-center px-6 text-center"><span className="mb-4 grid h-12 w-12 place-items-center rounded-full bg-secondary text-primary"><BadgeCheck size={22}/></span><div className="font-editorial text-xl">Nothing in this view</div><p className="max-w-sm text-xs leading-relaxed text-muted-foreground">{status==='pending'?'No proposals are awaiting a decision. Run reconciliation to stage the latest source review.':'No proposals match these filters.'}</p></div> :
          <div className="divide-y divide-border">
            {proposals.data.items.map((proposal,index)=><button key={proposal.id} onClick={()=>{setSelected(proposal);setReviewerNotes(proposal.reviewer_notes??'');}} data-testid={`button-proposal-${proposal.id}`} className={`focus-ring block min-h-[92px] w-full border-l-[3px] px-4 py-4 text-left transition-colors hover:bg-secondary/50 md:px-5 ${selectedProposal?.id===proposal.id?'border-l-primary bg-primary/[.035]':'border-l-transparent'}`}>
              <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-data text-[9px] uppercase tracking-[.1em] text-muted-foreground">{proposal.type.replaceAll('_',' ')}</span><StatusPill status={proposal.risk_level}/>{proposal.manual_review&&<span className="font-data text-[9px] text-accent">MANUAL REVIEW</span>}</div><div className="mt-1.5 truncate text-[13px] font-semibold">{proposal.title}</div><div className="mt-1 truncate text-[11px] text-muted-foreground">{proposal.community?.name??'Community unavailable'}{proposal.account?.name?` · ${proposal.account.name}`:''}</div></div><ArrowRight size={16} className="mt-5 shrink-0 text-muted-foreground"/></div>
            </button>)}
          </div>}
          <div className="border-t border-border px-4 py-3 text-[10px] text-muted-foreground md:px-5">Showing up to 50 proposals · last run {fmtDate(data?.last_run_at)}</div>
        </div>
        <ProposalDetail proposal={selectedProposal} notes={reviewerNotes} setNotes={setReviewerNotes} decide={makeDecision} busy={decision.isPending}/>
      </div>
    </section>
  </div>;
}

function ProposalDetail({proposal,notes,setNotes,decide,busy}:{proposal:Proposal|null;notes:string;setNotes:(s:string)=>void;decide:(d:'approved'|'rejected')=>void;busy:boolean}) {
  const health = useGetBellhavenReviewHealth();
  const isChow = proposal?.type === 'parent_change' && Number(proposal.account?.lifetime_revenue) > 0 && Number(proposal.account?.outstanding_ar) > 0;
  const canReject = proposal?.status==='pending';
  const canApprove = !!health.data?.crm_configured && proposal?.status==='pending' && proposal?.type !== 'ambiguous_match';
  const canRetry = !!health.data?.crm_configured && proposal?.status==='approved' && !['applied', 'applying'].includes(proposal?.crm_write_status ?? '');
  if (!proposal) return <aside className="hidden border-l border-border bg-[hsl(var(--muted)/.35)] p-6 lg:block"><div className="flex h-full min-h-[400px] flex-col items-center justify-center text-center"><span className="mb-4 grid h-12 w-12 place-items-center rounded-full border border-border bg-card text-muted-foreground"><ArrowDownLeft size={20}/></span><div className="font-editorial text-xl">Evidence lives here</div><p className="max-w-[230px] text-xs leading-relaxed text-muted-foreground">Choose a proposal to inspect the source, account snapshot, and proposed changes before deciding.</p><div className="mt-2 rounded-md border border-border bg-card px-3 py-2 font-data text-[9px] text-muted-foreground">NO SILENT WRITES</div></div></aside>;
  return <aside className="border-t border-border bg-[hsl(var(--muted)/.28)] p-4 md:p-5 lg:border-l lg:border-t-0"><div className="mb-4 flex items-start justify-between gap-2"><div><div className="font-data text-[9px] uppercase tracking-[.17em] text-primary">Proposal evidence</div><h3 className="mb-0 mt-2 font-editorial text-[23px] leading-tight">{proposal.title}</h3></div><StatusPill status={proposal.status}/></div>
    <p className="mb-4 mt-0 text-[12px] leading-relaxed text-muted-foreground">{proposal.summary}</p>
    <div className="mb-4 grid grid-cols-2 gap-2"><div className="rounded-lg border border-border bg-card p-3"><div className="font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Confidence</div><div className="mt-1 text-sm font-semibold">{Math.round(proposal.confidence*100)}%</div></div><div className="rounded-lg border border-border bg-card p-3"><div className="font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Risk</div><div className="mt-1"><StatusPill status={proposal.risk_level}/></div></div></div>
    <div className="space-y-3">
      {proposal.community&&<div className="rounded-lg border border-border bg-card p-3"><div className="mb-2 flex items-center gap-2 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground"><MapPin size={12}/>Source community</div><div className="text-xs font-semibold">{proposal.community.name}</div><div className="mt-1 text-[10px] text-muted-foreground">{proposal.community.address}, {proposal.community.city}, {proposal.community.state} {proposal.community.zip}</div><div className="mt-2 flex flex-wrap gap-1">{proposal.community.care_offerings?.map(c=><span key={c} className="rounded bg-secondary px-2 py-1 text-[9px]">{c}</span>)}</div><a href={proposal.community.source_url} target="_blank" rel="noreferrer" className="mt-2 inline-flex min-h-8 items-center gap-1 text-[10px] font-semibold text-primary hover:underline">Open source page <ExternalLink size={11}/></a></div>}
       {proposal.account&&<div className="rounded-lg border border-border bg-card p-3"><div className="mb-2 flex items-center gap-2 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground"><Building2 size={12}/>CRM account snapshot</div><div className="text-xs font-semibold">{proposal.account.name}</div><div className="mt-1 text-[10px] text-muted-foreground">{proposal.account.street}, {proposal.account.city}, {proposal.account.state} {proposal.account.zip}</div><div className="mt-2 grid grid-cols-2 gap-y-1 text-[10px]"><span className="text-muted-foreground">Account ID</span><span className="text-right font-data">{proposal.account.id}</span><span className="text-muted-foreground">Parent</span><span className="text-right">{proposal.account.parent_name??'—'}</span><span className="text-muted-foreground">Lifetime revenue</span><span className="text-right">{money(proposal.account.lifetime_revenue)}</span><span className="text-muted-foreground">Outstanding A/R</span><span className="text-right">{money(proposal.account.outstanding_ar)}</span><span className="text-muted-foreground">CRM status</span><span className="text-right">{proposal.account.status??'—'}</span><span className="text-muted-foreground">Duplicate of</span><span className="text-right font-data">{proposal.account.duplicate_of_account??'—'}</span><span className="text-muted-foreground">Current account</span><span className="text-right font-data">{proposal.account.chow_current_account??'—'}</span></div>{proposal.account.notes&&<div className="mt-3 border-t border-border pt-2"><div className="font-data text-[8px] uppercase tracking-[.12em] text-muted-foreground">CRM notes</div><p className="mb-0 mt-1 whitespace-pre-wrap break-words text-[10px] leading-relaxed">{proposal.account.notes}</p></div>}</div>}
    </div>
    {proposal.changes.length>0&&<div className="mt-4"><div className="mb-2 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Proposed field changes</div><div className="overflow-hidden rounded-lg border border-border bg-card">{proposal.changes.map((change,i)=><div key={`${change.field}-${i}`} className="grid grid-cols-[.8fr_1fr_14px_1fr] items-center gap-1 border-b border-border px-2.5 py-2 last:border-0"><span className="text-[9px] font-semibold">{isChow && change.field === 'parent_id' ? 'successor parent id' : change.field.replaceAll('_',' ')}</span><span className="truncate text-[9px] text-muted-foreground">{String(change.before??'—')}</span><ArrowRight size={11} className="text-accent"/><span className="truncate text-[9px] font-medium">{String(change.after??'—')}</span></div>)}</div></div>}
    {proposal.type==='parent_change'&&<p className="mt-4 text-xs font-semibold">CHOW safeguard: when revenue and A/R are both positive, approval creates a verified successor under the proposed parent and links the original account. Its original parent and billing history stay intact. Missing financial data blocks the write.</p>}
    {proposal.evidence.length>0&&<div className="mt-4"><div className="mb-2 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Evidence</div><ul className="m-0 space-y-2 pl-4 text-[10px] leading-relaxed text-muted-foreground">{proposal.evidence.map((item,i)=><li key={`${i}-${item}`}>{item}</li>)}</ul></div>}
    {proposal.status==='approved'&&proposal.crm_write_status!=='applied'&&<div className="mt-5 rounded-lg border border-[hsl(18_43%_53%/.35)] bg-[hsl(18_43%_53%/.07)] p-3 text-[10px] leading-relaxed"><div className="font-semibold">Approval saved · CRM update {proposal.crm_write_status.replaceAll('_',' ')}</div><p className="mb-0 mt-1 text-muted-foreground">{proposal.crm_write_error??'The CRM update has not completed yet.'}</p>{canRetry&&<button onClick={()=>decide('approved')} disabled={busy} data-testid="button-retry-approved-change" className="focus-ring mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 text-[11px] font-semibold text-primary-foreground disabled:opacity-50">{busy?<LoaderCircle size={14} className="animate-spin"/>:<RefreshCw size={14}/>}Retry approved change</button>}</div>}
    {proposal.status==='pending'&&<div className="mt-5 border-t border-border pt-4"><label htmlFor="reviewer-notes" className="mb-1.5 block text-[10px] font-semibold">Reviewer notes <span className="font-normal text-muted-foreground">optional</span></label><textarea id="reviewer-notes" value={notes} onChange={e=>setNotes(e.target.value)} rows={2} placeholder="Add context for this decision" data-testid="input-reviewer-notes" className="focus-ring w-full resize-y rounded-lg border border-border bg-card p-3 text-[11px] outline-none"/>
      {!health.data?.crm_configured&&<p className="mb-2 mt-2 text-[10px] leading-relaxed text-accent">Rejections can be recorded now. CRM approvals require the token to be configured.</p>}
      <div className="mt-3 grid grid-cols-2 gap-2"><button onClick={()=>decide('rejected')} disabled={busy||!canReject} data-testid="button-reject-proposal" className="focus-ring flex min-h-12 items-center justify-center gap-2 rounded-lg border border-border bg-card text-[11px] font-semibold text-destructive hover:bg-destructive/5 disabled:cursor-not-allowed disabled:opacity-45"><X size={15}/>Reject</button><button onClick={()=>decide('approved')} disabled={busy||!canApprove} data-testid="button-approve-proposal" className="focus-ring flex min-h-12 items-center justify-center gap-2 rounded-lg bg-primary text-[11px] font-semibold text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45">{busy?<LoaderCircle size={15} className="animate-spin"/>:<Check size={15}/>}Approve</button></div>
    </div>}
    <div className="mt-4 font-data text-[8px] text-muted-foreground">STAGED {fmtDate(proposal.created_at)}</div>
  </aside>;
}

function Communities() {
  const query = useListBellhavenCommunities();
  const [search,setSearch] = useState('');
  const [care,setCare] = useState('all');
  const [selected,setSelected] = useState<Community|null>(null);
  const careOptions = useMemo(()=>Array.from(new Set((query.data??[]).flatMap(c=>c.care_offerings))).sort(),[query.data]);
  const communities = (query.data??[]).filter(c=>{
    const match = `${c.name} ${c.address} ${c.city} ${c.state} ${c.zip}`.toLowerCase().includes(search.toLowerCase());
    return match&&(care==='all'||c.care_offerings.includes(care));
  });
  return <div className="fade-in">
    <Header eyebrow="Source of truth / inventory" title="Community inventory" description="The latest locations and care offerings found on Bellhaven’s public website. Source records are view-only."/>
    <div className="mb-5 flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-center gap-2 text-xs"><span className="grid h-8 w-8 place-items-center rounded-lg bg-primary/10 text-primary"><Building2 size={16}/></span><span><strong>{query.data?.length??'—'}</strong> website locations</span></div><div className="flex flex-col gap-2 sm:flex-row"><div className="relative"><Search size={15} className="absolute left-3 top-3.5 text-muted-foreground"/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Name, city, ZIP…" aria-label="Search communities" data-testid="input-community-search" className="focus-ring h-11 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-xs outline-none sm:w-56"/></div><select value={care} onChange={e=>setCare(e.target.value)} aria-label="Filter by care offering" data-testid="select-care-filter" className="focus-ring h-11 rounded-lg border border-border bg-background px-3 text-xs sm:w-48"><option value="all">All care offerings</option>{careOptions.map(option=><option key={option} value={option}>{option}</option>)}</select></div></div>
    {query.isLoading?<LoadingRows/>:query.isError?<ErrorNotice message="Community inventory could not be loaded." retry={()=>void query.refetch()}/>:
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_330px]"><section className="overflow-hidden rounded-xl border border-border bg-card"><div className="hidden grid-cols-[1.5fr_1fr_1.4fr_.8fr] gap-3 border-b border-border bg-muted/60 px-4 py-3 font-data text-[8px] uppercase tracking-[.15em] text-muted-foreground sm:grid"><span>Community</span><span>Location</span><span>Care offered</span><span>Source</span></div>{communities.length===0?<div className="p-12 text-center"><div className="font-editorial text-xl">No locations match</div><p className="text-xs text-muted-foreground">Try a different name, location, or care type.</p></div>:<div className="divide-y divide-border">{communities.map(c=><button key={c.slug} onClick={()=>setSelected(c)} data-testid={`button-community-${c.slug}`} className={`focus-ring grid min-h-[86px] w-full grid-cols-1 gap-2 px-4 py-3 text-left hover:bg-secondary/50 sm:grid-cols-[1.5fr_1fr_1.4fr_.8fr] sm:items-center sm:gap-3 ${selected?.slug===c.slug?'bg-primary/[.04]':''}`}><span className="text-[12px] font-semibold">{c.name}<span className="mt-1 block text-[9px] font-normal text-muted-foreground sm:hidden">{c.address}, {c.city}, {c.state} {c.zip}</span></span><span className="hidden text-[10px] text-muted-foreground sm:block">{c.city}, {c.state}</span><span className="flex flex-wrap gap-1">{c.care_offerings.map(item=><span key={item} className="rounded bg-secondary px-2 py-1 text-[9px]">{item}</span>)}</span><span className="flex items-center gap-1 text-[10px] text-primary">View page <ExternalLink size={11}/></span></button>)}</div>}</section>
        <aside className="rounded-xl border border-border bg-card p-5"><div className="font-data text-[9px] uppercase tracking-[.16em] text-primary">Location record</div>{selected?<><h2 className="mb-1 mt-3 font-editorial text-2xl">{selected.name}</h2><div className="text-xs leading-relaxed text-muted-foreground">{selected.address}<br/>{selected.city}, {selected.state} {selected.zip}</div><div className="mt-5 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Care offerings</div><div className="mt-2 flex flex-wrap gap-1.5">{selected.care_offerings.map(x=><StatusPill key={x} status={x}/>)}</div><div className="mt-5 font-data text-[8px] uppercase tracking-[.14em] text-muted-foreground">Source slug</div><div className="mt-1 font-data text-[10px]">{selected.slug}</div><a href={selected.source_url} target="_blank" rel="noreferrer" className="mt-5 flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border text-xs font-semibold hover:bg-secondary">Open source website <ExternalLink size={14}/></a></>:<div className="flex min-h-52 flex-col items-center justify-center text-center"><MapPin size={21} className="mb-3 text-muted-foreground"/><p className="max-w-[210px] text-xs leading-relaxed text-muted-foreground">Select a location to inspect its full source record.</p></div>}</aside>
      </div>}
  </div>;
}

function ActivityPage() {
  const query = useListBellhavenReviewActivity();
  const [search,setSearch] = useState('');
  const [filter,setFilter] = useState('all');
  const items=(query.data??[]).filter(a=>(filter==='all'||a.decision===filter)&&`${a.title} ${a.reviewer_notes??''}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="fade-in"><Header eyebrow="Audit trail / decisions" title="Decision log" description="A record of prior explicit approvals and rejections, including the reviewer’s note where provided."/>
    <div className="mb-5 grid gap-3 sm:grid-cols-2"><Kpi label="Approved" value={query.data?.filter(a=>a.decision==='approved').length??'—'} note="Recorded decisions" icon={Check} tone="bg-primary/10 text-primary"/><Kpi label="Rejected" value={query.data?.filter(a=>a.decision==='rejected').length??'—'} note="Recorded decisions" icon={X} tone="bg-secondary text-accent"/></div>
    <section className="overflow-hidden rounded-xl border border-border bg-card"><div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="m-0 font-editorial text-[23px]">Recent decisions</h2><p className="mb-0 mt-1 text-[11px] text-muted-foreground">Activity is supplied by the review service.</p></div><div className="flex gap-2"><div className="relative flex-1"><Search size={14} className="absolute left-3 top-3.5 text-muted-foreground"/><input value={search} onChange={e=>setSearch(e.target.value)} aria-label="Search activity" placeholder="Search activity" data-testid="input-activity-search" className="focus-ring h-11 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-xs outline-none"/></div><select aria-label="Filter activity" value={filter} onChange={e=>setFilter(e.target.value)} data-testid="select-activity-filter" className="focus-ring h-11 rounded-lg border border-border bg-background px-3 text-xs"><option value="all">All decisions</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></div></div>
      {query.isLoading?<LoadingRows/>:query.isError?<div className="p-4"><ErrorNotice message="Decision activity could not be loaded." retry={()=>void query.refetch()}/></div>:items.length===0?<div className="flex min-h-[260px] flex-col items-center justify-center text-center"><History size={22} className="mb-3 text-muted-foreground"/><div className="font-editorial text-xl">No decisions to show</div><p className="text-xs text-muted-foreground">Approved and rejected proposals will appear here.</p></div>:<div className="divide-y divide-border">{items.map((item,index)=><div key={`${item.proposal_id}-${item.decided_at}`} data-testid={`activity-entry-${item.proposal_id}`} className="grid gap-3 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_120px_160px] sm:items-center md:px-5"><div className="min-w-0"><div className="truncate text-[12px] font-semibold">{item.title}</div><div className="mt-1 font-data text-[9px] text-muted-foreground">PROPOSAL {item.proposal_id}</div>{item.reviewer_notes&&<p className="mb-0 mt-2 text-[11px] leading-relaxed text-muted-foreground">“{item.reviewer_notes}”</p>}</div><div><StatusPill status={item.decision}/><div className="mt-1 text-[10px]">CRM: {item.crm_write_status.replaceAll('_',' ')}</div></div><div className="flex items-center gap-2 text-[10px] text-muted-foreground"><Clock3 size={13}/>{fmtDate(item.decided_at)}</div></div>)}</div>}
    </section>
  </div>;
}

function Router() {
  return <Switch><Route path="/"><Shell><Dashboard/></Shell></Route><Route path="/communities"><Shell><Communities/></Shell></Route><Route path="/activity"><Shell><ActivityPage/></Shell></Route><Route><NotFound/></Route></Switch>;
}

function App() {
  return <QueryClientProvider client={queryClient}><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}><Router/></WouterRouter></QueryClientProvider>;
}

export default App;
