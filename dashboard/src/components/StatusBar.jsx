export default function StatusBar({ connected, voiceActive }) {
  return (
    <div className="flex items-center justify-between bg-slate-900 border border-slate-700 rounded-lg px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="text-purple-400 font-bold text-lg tracking-widest">POTTS</span>
        <span className="text-slate-500 text-xs">v3</span>
      </div>

      <div className="flex items-center gap-4 text-sm">
        {/* Voice status */}
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${voiceActive ? 'bg-green-400 animate-pulse' : 'bg-slate-600'}`} />
          <span className={voiceActive ? 'text-green-400' : 'text-slate-500'}>
            {voiceActive ? 'Voice active' : 'Voice idle'}
          </span>
        </div>

        {/* WS connection */}
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? 'bg-blue-400' : 'bg-red-500 animate-pulse'}`} />
          <span className={connected ? 'text-blue-400' : 'text-red-400'}>
            {connected ? 'Connected' : 'Reconnecting…'}
          </span>
        </div>
      </div>
    </div>
  )
}
