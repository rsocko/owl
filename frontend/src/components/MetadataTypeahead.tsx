import { useId, useMemo, useState } from 'react';
import './MetadataTypeahead.css';

export interface MetadataOption {
  value: string;
  label: string;
}

interface TypeaheadProps {
  ariaLabel: string;
  options: MetadataOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

export function MetadataTypeahead({
  ariaLabel,
  options,
  value,
  onChange,
  placeholder = 'Search…',
  disabled = false,
  className = '',
}: TypeaheadProps & {
  value: string;
  onChange: (value: string) => void;
}) {
  const listboxId = useId();
  const selected = options.find((option) => option.value === value);
  const [query, setQuery] = useState(selected?.label ?? '');
  const [open, setOpen] = useState(false);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized || selected?.label === query) return options;
    return options.filter((option) => option.label.toLocaleLowerCase().includes(normalized));
  }, [options, query, selected?.label]);

  const choose = (option: MetadataOption) => {
    onChange(option.value);
    setQuery(option.label);
    setOpen(false);
  };

  return (
    <div className={`metadata-typeahead ${className}`}>
      <div className="metadata-typeahead-input-wrap">
        <input
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open}
          autoComplete="off"
          disabled={disabled}
          placeholder={placeholder}
          value={query}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => {
            setOpen(false);
            setQuery(selected?.label ?? '');
          }, 100)}
          onChange={(event) => {
            setQuery(event.target.value);
            onChange('');
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && filtered[0]) {
              event.preventDefault();
              choose(filtered[0]);
            } else if (event.key === 'Escape') {
              setOpen(false);
              setQuery(selected?.label ?? '');
            }
          }}
        />
        {value ? (
          <button
            type="button"
            className="metadata-typeahead-clear"
            aria-label={`Clear ${ariaLabel.toLowerCase()}`}
            onClick={() => {
              onChange('');
              setQuery('');
            }}
          >
            ×
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="metadata-typeahead-list" id={listboxId} role="listbox">
          {filtered.length === 0 ? (
            <span className="metadata-typeahead-empty">No matches</span>
          ) : filtered.map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={option.value === value}
              key={option.value}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function MetadataMultiTypeahead({
  ariaLabel,
  options,
  values,
  onChange,
  placeholder = 'Search and add…',
  disabled = false,
  className = '',
  minimumSelections = 0,
}: TypeaheadProps & {
  values: string[];
  onChange: (values: string[]) => void;
  minimumSelections?: number;
}) {
  const listboxId = useId();
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const labelsByValue = useMemo(
    () => new Map(options.map((option) => [option.value, option.label])),
    [options],
  );
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return options.filter((option) =>
      !values.includes(option.value)
      && (!normalized || option.label.toLocaleLowerCase().includes(normalized)));
  }, [options, query, values]);

  const add = (option: MetadataOption) => {
    onChange([...values, option.value]);
    setQuery('');
    setOpen(false);
  };

  return (
    <div className={`metadata-multi-typeahead ${className}`}>
      <div className="metadata-typeahead-values">
        {values.length === 0 ? <span className="metadata-typeahead-empty">None</span> : values.map((value) => (
          <span className="metadata-typeahead-value" key={value}>
            {labelsByValue.get(value) ?? 'Unavailable metadata'}
            <button
              type="button"
              disabled={disabled || values.length <= minimumSelections}
              aria-label={`Remove ${labelsByValue.get(value) ?? 'metadata'}`}
              onClick={() => onChange(values.filter((selected) => selected !== value))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="metadata-typeahead">
        <input
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open}
          autoComplete="off"
          disabled={disabled}
          placeholder={placeholder}
          value={query}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => {
            setOpen(false);
            setQuery('');
          }, 100)}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && filtered[0]) {
              event.preventDefault();
              add(filtered[0]);
            } else if (event.key === 'Escape') {
              setOpen(false);
              setQuery('');
            }
          }}
        />
        {open ? (
          <div className="metadata-typeahead-list" id={listboxId} role="listbox">
            {filtered.length === 0 ? (
              <span className="metadata-typeahead-empty">No matches</span>
            ) : filtered.map((option) => (
              <button
                type="button"
                role="option"
                aria-selected={false}
                key={option.value}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => add(option)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
