'use client';

import { weekdays } from '../resource-summary';
import { windowKey, type ConfigurationDraft } from './configuration-fields';
import styles from '../admin-configuration.module.css';

export function WeeklyWindows({
  draft,
  onChange,
  errors,
  disabled,
}: {
  draft: ConfigurationDraft;
  onChange: (next: ConfigurationDraft) => void;
  errors: Record<string, string>;
  disabled: boolean;
}) {
  return (
    <div id="configuration-windows">
      <p>
        Giờ địa phương theo <strong>{draft.timezone}</strong>. Các khoảng tiếp giáp được giữ riêng;
        một buổi gặp phải vừa trong một khoảng.
      </p>
      {weekdays.map((day, index) => (
        <fieldset className={styles.day} key={day} disabled={disabled}>
          <legend>{day}</legend>
          {!draft.windows.some((w) => w.weekday === index + 1) && <p>Đóng cửa</p>}
          {draft.windows
            .filter((w) => w.weekday === index + 1)
            .map((w, row) => (
              <div key={w.key}>
                <div className={styles.window}>
                  <label>
                    Mở cửa {day}, khoảng {row + 1}
                    <input
                      id={`configuration-${w.key}`}
                      type="time"
                      step="1"
                      value={w.opens_at}
                      aria-invalid={Boolean(errors[w.key])}
                      aria-describedby={errors[w.key] ? `error-${w.key}` : undefined}
                      onChange={(e) =>
                        onChange({
                          ...draft,
                          windows: draft.windows.map((item) =>
                            item.key === w.key ? { ...item, opens_at: e.target.value } : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Đóng cửa {day}, khoảng {row + 1}
                    <input
                      type="time"
                      step="1"
                      value={w.closes_at}
                      aria-invalid={Boolean(errors[w.key])}
                      aria-describedby={errors[w.key] ? `error-${w.key}` : undefined}
                      onChange={(e) =>
                        onChange({
                          ...draft,
                          windows: draft.windows.map((item) =>
                            item.key === w.key ? { ...item, closes_at: e.target.value } : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      onChange({
                        ...draft,
                        windows: draft.windows.filter((item) => item.key !== w.key),
                      })
                    }
                  >
                    Xóa {day}, khoảng {row + 1}
                  </button>
                </div>
                {errors[w.key] && (
                  <p className={styles.error} id={`error-${w.key}`}>
                    {errors[w.key]}
                  </p>
                )}
              </div>
            ))}
          <button
            type="button"
            onClick={() =>
              onChange({
                ...draft,
                windows: [
                  ...draft.windows,
                  { key: windowKey(), weekday: index + 1, opens_at: '', closes_at: '' },
                ],
              })
            }
          >
            Thêm khoảng {day}
          </button>
        </fieldset>
      ))}
    </div>
  );
}
