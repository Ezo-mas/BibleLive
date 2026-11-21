import { useRef, useState } from 'react'
import type { RefObject } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { Bridge, Form, Network, Options, Platform, StartError, UILanguage } from './bridge'
import { Choice } from './Choice'
import { Icon } from './Icon'

const tabs = ['speech', 'network', 'launch'] as const
const tabIcons = { speech: 'speech', network: 'network', launch: 'sliders' } as const
export type SettingsTab = (typeof tabs)[number]
type SelectKey =
  'speech_engine' | 'device' | 'model' | 'compute_type' | 'auto_mode' | 'ui_language' | 'log_level'

interface Props {
  tab: SettingsTab
  onTab: (tab: SettingsTab) => void
  form: Form
  options: Options
  locked: boolean
  platform: Platform
  network: Network | null
  error: StartError | null
  portRef: RefObject<HTMLInputElement>
  argsRef: RefObject<HTMLInputElement>
  onChange: <K extends keyof Form>(key: K, value: Form[K]) => void
  onLanguage: (code: UILanguage) => void
  bridge: Bridge
  onFirewall: () => void
}

function NetworkDetails({
  network,
  bridge,
  onFirewall,
}: Pick<Props, 'bridge' | 'onFirewall'> & { network: Network }) {
  const { t } = useI18n()
  const [feedback, setFeedback] = useState('')
  const { display_url, operator_url, token, firewall, operator_qr } = network
  const fields = [
    ['displayUrl', display_url],
    ['operatorUrl', operator_url],
    ['operatorToken', token],
  ] as const

  async function copy(value: string) {
    try {
      try {
        await navigator.clipboard.writeText(value)
      } catch {
        await bridge.copy_text(value)
      }
      setFeedback(t('launcher.copied'))
    } catch {
      setFeedback(t('launcher.copyFailed'))
    }
  }

  return (
    <section className="bl-pairing" aria-label={t('launcher.networkDetails')}>
      {fields.map(([key, value]) => (
        <div className="bl-copy-row" key={key}>
          <label className="bl-field">
            <span>{t(`launcher.${key}`)}</span>
            <input type="text" value={value} readOnly />
          </label>
          <button
            className="bl-button"
            type="button"
            disabled={!value}
            aria-label={t('launcher.copyLabel', { field: t(`launcher.${key}`) })}
            onClick={() => void copy(value)}
          >
            {t('launcher.copy')}
          </button>
        </div>
      ))}
      {(!display_url || !operator_url) && (
        <p className="bl-muted">{t('launcher.noNetworkAddress')}</p>
      )}
      <p className="bl-muted">{t('launcher.operatorAccessNote')}</p>
      {operator_qr && (
        <figure className="bl-operator-qr">
          <img src={operator_qr} alt={t('launcher.operatorQr')} />
          <figcaption>
            <strong>{t('launcher.operatorQr')}</strong>
            <span className="bl-error">{t('launcher.operatorQrWarning')}</span>
          </figcaption>
        </figure>
      )}
      {firewall && (
        <div className="bl-notice bl-firewall">
          <p>{firewall.message}</p>
          <button
            type="button"
            className="bl-button"
            disabled={!firewall.button_enabled}
            onClick={onFirewall}
          >
            {t('launcher.allowFirewall')}
          </button>
        </div>
      )}
      {feedback && (
        <p className="bl-muted" role="status">
          {feedback}
        </p>
      )}
    </section>
  )
}

export function Settings({
  tab,
  onTab,
  form,
  options,
  locked,
  platform,
  network,
  error,
  portRef,
  argsRef,
  onChange,
  onLanguage,
  bridge,
  onFirewall,
}: Props) {
  const { t } = useI18n()
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const lithuanian = form.language === 'lt'
  const usesWhisper = form.language === 'en' || form.speech_engine === 'whisper'
  const portError = error?.field === 'port' ? error.text : null
  const argsError = error?.field === 'extra_args' ? error.text : null

  function select(key: SelectKey, label: string) {
    return (
      <Choice
        label={label}
        value={form[key]}
        options={options[key]}
        disabled={locked && key !== 'ui_language'}
        onChange={value => {
          if (key === 'ui_language') {
            if (value === 'auto' || value === 'en' || value === 'lt') onLanguage(value)
          } else if (value !== null) {
            onChange(key, value)
          }
        }}
      />
    )
  }

  return (
    <section className="bl-settings" aria-label={t('launcher.advancedSettings')}>
      <div className="bl-sections" role="tablist" aria-label={t('launcher.settingsSections')}>
        {tabs.map((name, index) => (
          <button
            type="button"
            key={name}
            id={`bl-tab-${name}`}
            role="tab"
            aria-controls={`bl-panel-${name}`}
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            ref={element => {
              tabRefs.current[index] = element
            }}
            onClick={() => onTab(name)}
            onKeyDown={event => {
              const next = {
                ArrowRight: (index + 1) % tabs.length,
                ArrowLeft: (index + tabs.length - 1) % tabs.length,
                Home: 0,
                End: tabs.length - 1,
              }[event.key as 'ArrowRight' | 'ArrowLeft' | 'Home' | 'End']
              if (next === undefined) return
              event.preventDefault()
              onTab(tabs[next])
              tabRefs.current[next]?.focus()
            }}
          >
            <Icon name={tabIcons[name]} /> {t(`launcher.tab.${name}`)}
          </button>
        ))}
      </div>
      <section
        className="bl-panel"
        id="bl-panel-speech"
        role="tabpanel"
        aria-labelledby="bl-tab-speech"
        hidden={tab !== 'speech'}
      >
        <h2>{t('launcher.speechProcessing')}</h2>
        <div className="bl-engine">
          <div>
            <Icon name="speech" />
            <strong>
              {t(lithuanian ? 'launcher.lithuanianSpeech' : 'launcher.englishSpeech')}
            </strong>
          </div>
          <p className="bl-muted">
            {t(
              !lithuanian
                ? 'launcher.englishHint'
                : form.speech_engine === 'auto'
                  ? 'launcher.engineAutoHint'
                  : 'launcher.engineHint'
            )}
          </p>
          {lithuanian && select('speech_engine', t('launcher.engine'))}
        </div>
        {select('device', t('launcher.processingDevice'))}
        {usesWhisper && (
          <details className="bl-details">
            <summary>{t('launcher.whisperTuning')}</summary>
            <div className="bl-fields">
              {select('model', t('launcher.model'))}
              {select('compute_type', t('launcher.computeType'))}
            </div>
          </details>
        )}
        <details className="bl-details">
          <summary>{t('launcher.troubleshooting')}</summary>
          <label className="bl-field">
            <span>{t('launcher.extraArgs')}</span>
            <input
              ref={argsRef}
              type="text"
              value={form.extra_args}
              disabled={locked}
              placeholder={t('launcher.extraArgsPlaceholder')}
              aria-invalid={argsError ? true : undefined}
              aria-describedby={argsError ? 'bl-args-error' : undefined}
              onChange={event => onChange('extra_args', event.currentTarget.value)}
            />
          </label>
          {argsError && (
            <p id="bl-args-error" className="bl-error" role="alert">
              {argsError}
            </p>
          )}
        </details>
      </section>
      <section
        className="bl-panel"
        id="bl-panel-network"
        role="tabpanel"
        aria-labelledby="bl-tab-network"
        hidden={tab !== 'network'}
      >
        <h2>{t('launcher.localNetwork')}</h2>
        <div className="bl-fields">
          <label className="bl-field">
            <span>{t('launcher.access')}</span>
            <select
              value={String(form.allow_network)}
              disabled={locked}
              onChange={event => onChange('allow_network', event.currentTarget.value === 'true')}
            >
              <option value="false">{t('launcher.localOnly')}</option>
              <option value="true">{t('launcher.networkAccess')}</option>
            </select>
          </label>
          <label className="bl-field">
            <span>{t('launcher.port')}</span>
            <input
              ref={portRef}
              type="number"
              min={1}
              max={65535}
              step={1}
              value={form.port}
              disabled={locked}
              aria-invalid={portError ? true : undefined}
              aria-describedby={portError ? 'bl-port-error' : undefined}
              onChange={event => onChange('port', event.currentTarget.value)}
            />
          </label>
        </div>
        {portError && (
          <p id="bl-port-error" className="bl-error" role="alert">
            {portError}
          </p>
        )}
        {form.allow_network && platform === 'darwin' && (
          <div className="bl-notice">
            <strong>{t('launcher.macTitle')}</strong>
            <p>{t('launcher.macNote')}</p>
          </div>
        )}
        {tab === 'network' && network && (
          <NetworkDetails network={network} bridge={bridge} onFirewall={onFirewall} />
        )}
      </section>
      <section
        className="bl-panel"
        id="bl-panel-launch"
        role="tabpanel"
        aria-labelledby="bl-tab-launch"
        hidden={tab !== 'launch'}
      >
        <h2>{t('launcher.launchOptions')}</h2>
        <div className="bl-fields">
          {select('auto_mode', t('launcher.autoApprove'))}
          {select('ui_language', t('launcher.interfaceLanguage'))}
          {select('log_level', t('launcher.serviceLogLevel'))}
        </div>
        <div className="bl-checks">
          {(['open_operator', 'open_display', 'session_audio'] as const).map(key => (
            <label key={key}>
              <input
                type="checkbox"
                checked={form[key]}
                disabled={locked}
                onChange={event => onChange(key, event.currentTarget.checked)}
              />
              {t(`launcher.checkbox.${key}`)}
            </label>
          ))}
        </div>
      </section>
    </section>
  )
}
