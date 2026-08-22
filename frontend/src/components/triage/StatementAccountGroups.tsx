import type { SeriesDoc } from './StatementGroupingDetail';
import './statement-grouping.css';

interface Props {
  correspondentName: string;
  documents: SeriesDoc[];
  accounts: string[];
  accountColorMap: Record<string, string>;
}

export function StatementAccountGroups({
  correspondentName,
  documents,
  accounts,
  accountColorMap,
}: Props) {
  if (accounts.length < 2) return null;

  return (
    <section className="sg-account-groups" aria-label="Detected statement account groups">
      <div className="sg-account-groups-heading">
        <strong>Shared correspondent, different statements</strong>
        <span>
          {correspondentName} documents contain {accounts.length} account identifiers and are
          likely separate statement series.
        </span>
      </div>
      <div className="sg-account-group-grid">
        {accounts.map(account => {
          const accountDocuments = documents.filter(doc => doc.account_hint === account);
          const exampleTitle = accountDocuments.find(doc => doc.title)?.title;
          return (
            <div className="sg-account-group" key={account}>
              <span
                className="sg-account-group-dot"
                style={{ background: accountColorMap[account] }}
              />
              <div>
                <div className="sg-account-group-name">Account {account}</div>
                <div className="sg-account-group-meta">
                  {accountDocuments.length} document{accountDocuments.length === 1 ? '' : 's'}
                  {exampleTitle ? ` · ${exampleTitle}` : ''}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
