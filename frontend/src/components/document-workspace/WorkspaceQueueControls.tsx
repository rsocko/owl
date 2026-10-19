import * as Popover from '@radix-ui/react-popover';
import {
  Check,
  ChevronDown,
  ListFilter,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react';

export interface WorkspaceQueueOption {
  value: string;
  label: string;
  count?: number;
}

export interface WorkspaceQueueFilterGroup {
  label: string;
  mode: 'single' | 'multiple';
  options: WorkspaceQueueOption[];
  selected: string | string[];
  onSelect: (value: string) => void;
}

export interface WorkspaceQueueActiveFilter {
  key: string;
  label: string;
  count?: number;
  onRemove: () => void;
}

interface WorkspaceQueueControlsProps {
  search: {
    value: string;
    placeholder: string;
    ariaLabel: string;
    onChange: (value: string) => void;
  };
  primary: {
    value: string;
    ariaLabel: string;
    options: WorkspaceQueueOption[];
    onChange: (value: string) => void;
  };
  filterLabel?: string;
  filterGroups?: WorkspaceQueueFilterGroup[];
  activeFilters?: WorkspaceQueueActiveFilter[];
  onClearFilters?: () => void;
  sort: {
    value: string;
    options: WorkspaceQueueOption[];
    onChange: (value: string) => void;
  };
}

function isSelected(group: WorkspaceQueueFilterGroup, value: string) {
  return Array.isArray(group.selected)
    ? group.selected.includes(value)
    : group.selected === value;
}

export default function WorkspaceQueueControls({
  search,
  primary,
  filterLabel = 'Filters',
  filterGroups = [],
  activeFilters = [],
  onClearFilters,
  sort,
}: WorkspaceQueueControlsProps) {
  const selectedPrimary = primary.options.find((option) => option.value === primary.value)
    ?? primary.options[0];
  const selectedSort = sort.options.find((option) => option.value === sort.value)
    ?? sort.options[0];

  return (
    <div className="workspace-queue-controls">
      <label className="document-workspace-search">
        <Search size={16} />
        <input
          aria-label={search.ariaLabel}
          placeholder={search.placeholder}
          value={search.value}
          onChange={(event) => search.onChange(event.target.value)}
        />
      </label>

      <div className={`workspace-control-row${filterGroups.length ? '' : ' without-filters'}`}>
        <Popover.Root>
          <Popover.Trigger asChild>
            <button type="button" className="workspace-control workspace-primary-control" aria-label={primary.ariaLabel}>
              <span>
                {selectedPrimary?.label}
                {selectedPrimary?.count != null ? <b>{selectedPrimary.count}</b> : null}
              </span>
              <ChevronDown size={15} />
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="workspace-queue-menu workspace-primary-menu" sideOffset={6} align="start">
              <div role="radiogroup" aria-label={primary.ariaLabel}>
                {primary.options.map((option) => {
                  const selected = option.value === primary.value;
                  return (
                    <Popover.Close asChild key={option.value}>
                      <button
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        aria-label={`${option.label}${option.count != null ? ` ${option.count}` : ''}`}
                        className={selected ? 'selected' : ''}
                        onClick={() => primary.onChange(option.value)}
                      >
                        <span>{option.label}</span>
                        {option.count != null ? <b>{option.count}</b> : null}
                        <Check size={14} aria-hidden="true" />
                      </button>
                    </Popover.Close>
                  );
                })}
              </div>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>

        {filterGroups.length ? (
          <Popover.Root>
            <Popover.Trigger asChild>
              <button type="button" className="workspace-control workspace-filter-control">
                <SlidersHorizontal size={15} />
                <span>{filterLabel}</span>
                {activeFilters.length ? <b>{activeFilters.length}</b> : null}
              </button>
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content className="workspace-queue-filter-popover" sideOffset={6} align="start">
                <div className="workspace-filter-popover-heading">
                  <strong>{filterLabel}</strong>
                  {activeFilters.length && onClearFilters ? (
                    <button type="button" onClick={onClearFilters}>Clear all</button>
                  ) : null}
                </div>
                {filterGroups.map((group) => (
                  <section className="workspace-filter-popover-group" key={group.label}>
                    <span>{group.label}</span>
                    <div
                      role={group.mode === 'single' ? 'radiogroup' : 'group'}
                      aria-label={group.label}
                    >
                      {group.options.map((option) => {
                        const selected = isSelected(group, option.value);
                        return (
                          <button
                            type="button"
                            key={option.value}
                            role={group.mode === 'single' ? 'radio' : 'checkbox'}
                            aria-checked={selected}
                            aria-label={`${option.label}${option.count != null ? ` ${option.count}` : ''}`}
                            className={selected ? 'selected' : ''}
                            onClick={() => group.onSelect(option.value)}
                          >
                            <i aria-hidden="true"><Check size={12} /></i>
                            <span>{option.label}</span>
                            {option.count != null ? <b>{option.count}</b> : null}
                          </button>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
        ) : null}

        <Popover.Root>
          <Popover.Trigger asChild>
            <button
              type="button"
              className="workspace-control workspace-sort-control"
              aria-label={`Sort: ${selectedSort?.label ?? 'Choose order'}`}
              title={`Sort: ${selectedSort?.label ?? 'Choose order'}`}
            >
              <ListFilter size={16} />
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="workspace-queue-menu workspace-sort-menu" sideOffset={6} align="end">
              <div role="radiogroup" aria-label="Sort order">
                {sort.options.map((option) => {
                  const selected = option.value === sort.value;
                  return (
                    <Popover.Close asChild key={option.value}>
                      <button
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        className={selected ? 'selected' : ''}
                        onClick={() => sort.onChange(option.value)}
                      >
                        <span>{option.label}</span>
                        <Check size={14} aria-hidden="true" />
                      </button>
                    </Popover.Close>
                  );
                })}
              </div>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      </div>

      {activeFilters.length ? (
        <div className="workspace-active-filters" aria-label="Active filters">
          {activeFilters.map((filter) => (
            <button type="button" key={filter.key} onClick={filter.onRemove} aria-label={`Remove ${filter.label} filter`}>
              <span>{filter.label}</span>
              {filter.count != null ? <b>{filter.count}</b> : null}
              <X size={12} aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
