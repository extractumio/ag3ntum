import React, { useState, useRef, useEffect, useCallback } from 'react';

// ANSI Color mapping to CSS
const COLORS = {
  black: '#1e1e1e',
  red: '#cd3131',
  green: '#0dbc79',
  yellow: '#e5e510',
  blue: '#2472c8',
  magenta: '#bc3fbc',
  cyan: '#11a8cd',
  white: '#e5e5e5',
  brightBlack: '#666666',
  brightRed: '#f14c4c',
  brightGreen: '#23d18b',
  brightYellow: '#f5f543',
  brightBlue: '#3b8eea',
  brightMagenta: '#d670d6',
  brightCyan: '#29b8db',
  brightWhite: '#ffffff',
  bg: '#1a1a1a',
  bgSecondary: '#212121',
  dim: '#444444',
};

// Session history for dropdown
const sessionHistory = [
  { id: '71132667-30ed-4840-a576-a443e622b58f', date: '2025-12-30 20:18', task: 'fetch the content of this url...', status: 'failed' },
  { id: 'a2c45891-12ab-4cd3-ef56-789012345678', date: '2025-12-30 19:45', task: 'create a python script for...', status: 'success' },
  { id: 'b3d56902-23bc-5de4-f067-890123456789', date: '2025-12-30 18:22', task: 'analyze the codebase and...', status: 'success' },
  { id: 'c4e67013-34cd-6ef5-a178-901234567890', date: '2025-12-29 14:10', task: 'refactor the authentication...', status: 'failed' },
];

// Sample data to demonstrate the interface
const sampleMessages = [
  {
    type: 'session-start',
    content: {
      sessionId: '71132667-30ed-4840-a576-a443e622b58f',
      model: 'claude-sonnet-4-5-20250929',
      workingDir: '/Users/greg/CLOUDLINUX.../workspace',
      startedAt: '20:18:57',
    }
  },
  {
    type: 'profile',
    content: {
      name: 'USER',
      id: 'user:20251230_201857_18d24b49',
      permissions: { allow: 16, deny: 7 },
      loaded: '...-momus/AGENT/config/permissions.user.yaml',
    }
  },
  {
    type: 'tools',
    content: {
      count: 9,
      tools: ['Edit', 'Write', 'Task', 'Bash', 'Skill', 'Read', 'TodoRead', 'MultiEdit', 'TodoWrite']
    }
  },
  {
    type: 'task',
    content: 'fetch the content of this url and summarize what is this about\nhttps://github.com/extractumio/extractum-skills/blob/main/general-purpose/create-claude-agent...'
  },
  {
    type: 'thinking',
    content: "I'll fetch the content from that GitHub URL and summarize it for you.",
    chars: 69
  },
  {
    type: 'action',
    content: {
      index: 1,
      tool: 'Bash',
      command: 'curl -s https://raw.githubusercontent.com/extractumio/extractum-skills/main/g...',
      description: 'Fetch content from GitHub URL',
    }
  },
  {
    type: 'permission',
    content: { tool: 'Bash', action: 'deny' }
  },
  {
    type: 'thinking',
    content: "Let me try an alternative approach using Python to fetch the URL:",
    chars: 65
  },
  {
    type: 'action',
    content: {
      index: 2,
      tool: 'Write',
      file_path: './fetch_url.py',
      preview: 'import urllib.request\nimport sys\n\nurl = "https://raw.githubusercontent.com/ex..."',
    }
  },
  {
    type: 'permission',
    content: { tool: 'Write', action: 'allow' }
  },
  {
    type: 'action',
    content: {
      index: 3,
      tool: 'Bash',
      command: 'python ./fetch_url.py',
      description: 'Execute Python script to fetch URL',
    }
  },
  {
    type: 'permission',
    content: { tool: 'Bash', action: 'deny' }
  },
  {
    type: 'thinking',
    content: "I cannot fetch external URLs due to security restrictions in the sandbox environment...",
    chars: 116
  },
  {
    type: 'action',
    content: {
      index: 4,
      tool: 'Write',
      file_path: './report.md',
      preview: '# Report\n\nUnable to fetch external URLs due to sandbox restrictions.',
    }
  },
  {
    type: 'permission',
    content: { tool: 'Write', action: 'allow' }
  },
  {
    type: 'result',
    content: {
      status: 'FAILED',
      duration: '24.3s',
      turns: 5,
      cost: '$0.08',
      message: 'I was unable to complete the task because network access to external URLs is blocked by the sandbox security policy. The system does not permit fetching content from external websites, including the GitHub URL you provided.'
    }
  }
];

// Component for rendering different message types
const MessageRenderer = ({ message, isActive, isLatest }) => {
  const dimStyle = !isActive && !isLatest ? { opacity: 0.7 } : {};
  
  switch (message.type) {
    case 'session-start':
      return (
        <div className="mb-3" style={dimStyle}>
          <div className="flex items-center gap-2">
            <span style={{ color: COLORS.brightCyan }}>::</span>
            <span style={{ color: COLORS.brightCyan }}>Ag3ntum</span>
            <span style={{ color: COLORS.dim }}>|</span>
            <span style={{ color: COLORS.brightCyan }}>Self-Improving Agent</span>
          </div>
          <div className="ml-4 mt-1 text-xs" style={{ color: COLORS.dim }}>
            <span>{message.content.model}</span>
            <span className="mx-2">•</span>
            <span>{message.content.workingDir}</span>
            <span className="mx-2">•</span>
            <span>{message.content.startedAt}</span>
          </div>
        </div>
      );

    case 'profile':
      return (
        <div className="mb-1 text-sm" style={dimStyle}>
          <span style={{ color: COLORS.dim }}>profile:</span>
          <span className="ml-2" style={{ color: COLORS.brightYellow }}>{message.content.name}</span>
          <span className="ml-2" style={{ color: COLORS.dim }}>
            [allow={message.content.permissions.allow}, deny={message.content.permissions.deny}]
          </span>
        </div>
      );

    case 'tools':
      return (
        <div className="mb-3 text-sm" style={dimStyle}>
          <span style={{ color: COLORS.dim }}>tools:</span>
          <span className="ml-2" style={{ color: COLORS.green }}>{message.content.count}</span>
          <span className="ml-1" style={{ color: COLORS.dim }}>
            ({message.content.tools.join(', ')})
          </span>
        </div>
      );

    case 'task':
      return (
        <div className="my-3" style={dimStyle}>
          <div style={{ color: COLORS.brightWhite }}>
            <span style={{ color: COLORS.brightCyan }}>TASK</span>
            <span className="ml-2" style={{ color: COLORS.white }}>{message.content}</span>
          </div>
        </div>
      );

    case 'thinking':
      return (
        <div className="my-2 text-sm" style={dimStyle}>
          <span style={{ color: COLORS.brightYellow }}>❯</span>
          <span className="ml-2" style={{ color: COLORS.white }}>{message.content}</span>
          <span className="ml-2" style={{ color: COLORS.dim }}>({message.chars})</span>
        </div>
      );

    case 'action':
      const { index, tool, command, file_path, preview } = message.content;
      
      return (
        <div className="my-1 text-sm" style={dimStyle}>
          <span style={{ color: COLORS.green }}>⊙</span>
          <span className="ml-1" style={{ color: COLORS.dim }}>[{index}]</span>
          <span className="ml-2" style={{ color: COLORS.brightBlue }}>{tool}</span>
          {command && <span className="ml-2" style={{ color: COLORS.white }}>{command}</span>}
          {file_path && <span className="ml-2" style={{ color: COLORS.white }}>{file_path}</span>}
          {preview && (
            <pre className="ml-6 mt-1 text-xs" style={{ color: COLORS.dim }}>{preview}</pre>
          )}
        </div>
      );

    case 'permission':
      const isAllow = message.content.action === 'allow';
      return (
        <div className="ml-4 text-sm" style={dimStyle}>
          <span style={{ color: isAllow ? COLORS.green : COLORS.red }}>
            {isAllow ? '✓' : '×'}
          </span>
          <span className="ml-1" style={{ color: COLORS.dim }}>
            {message.content.tool} →
          </span>
          <span className="ml-1" style={{ 
            color: isAllow ? COLORS.dim : COLORS.red, 
            fontWeight: isAllow ? 'normal' : 'bold' 
          }}>
            {message.content.action}
          </span>
        </div>
      );

    case 'result':
      const isFailed = message.content.status === 'FAILED';
      return (
        <div className="mt-4">
          <div className="flex items-center gap-3 mb-2">
            <span style={{ color: isFailed ? COLORS.brightRed : COLORS.brightGreen, fontWeight: 'bold' }}>
              {isFailed ? '× FAILED' : '✓ SUCCESS'}
            </span>
            <span style={{ color: COLORS.dim }}>
              {message.content.duration} • {message.content.turns} turns • {message.content.cost}
            </span>
          </div>
          <div style={{ color: COLORS.yellow }}>{message.content.message}</div>
        </div>
      );

    case 'user-input':
      return (
        <div className="my-3">
          <span style={{ color: COLORS.brightGreen }}>❯</span>
          <span className="ml-2" style={{ color: COLORS.brightWhite }}>{message.content}</span>
        </div>
      );

    default:
      return (
        <div className="text-sm" style={{ color: COLORS.white, ...dimStyle }}>
          {JSON.stringify(message.content)}
        </div>
      );
  }
};

// Menu Bar Component
const MenuBar = ({ onAboutClick, onNewSession, currentSessionId, sessions, onSelectSession, onCollapseAll, onExpandAll }) => {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="flex items-center justify-between px-4 py-2 text-sm" 
         style={{ backgroundColor: COLORS.bgSecondary }}>
      <div className="flex items-center gap-4">
        <span style={{ color: COLORS.brightCyan }}>[::] AGENT</span>
        
        {/* Session Dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="flex items-center gap-2 px-2 py-1 cursor-pointer"
            style={{ color: COLORS.white, backgroundColor: COLORS.bg }}
          >
            <span className="text-xs truncate max-w-32 sm:max-w-48" style={{ color: COLORS.dim }}>
              {currentSessionId.slice(0, 8)}...
            </span>
            <span style={{ color: COLORS.dim, fontSize: '10px' }}>▼</span>
          </button>
          
          {dropdownOpen && (
            <div 
              className="absolute top-full left-0 mt-1 w-72 max-h-64 overflow-auto z-50"
              style={{ backgroundColor: COLORS.bg, border: `1px solid ${COLORS.dim}` }}
            >
              {sessions.map((session) => (
                <button
                  key={session.id}
                  onClick={() => {
                    onSelectSession(session.id);
                    setDropdownOpen(false);
                  }}
                  className="w-full text-left px-3 py-2 text-xs hover:bg-opacity-50 transition-colors"
                  style={{ 
                    backgroundColor: session.id === currentSessionId ? COLORS.bgSecondary : 'transparent',
                  }}
                  onMouseEnter={(e) => e.target.style.backgroundColor = COLORS.bgSecondary}
                  onMouseLeave={(e) => e.target.style.backgroundColor = session.id === currentSessionId ? COLORS.bgSecondary : 'transparent'}
                >
                  <div className="flex items-center justify-between">
                    <span style={{ color: COLORS.white }}>{session.id.slice(0, 8)}...</span>
                    <span style={{ color: session.status === 'success' ? COLORS.green : COLORS.red }}>
                      {session.status === 'success' ? '✓' : '×'}
                    </span>
                  </div>
                  <div className="truncate mt-1" style={{ color: COLORS.dim }}>{session.task}</div>
                  <div style={{ color: COLORS.dim }}>{session.date}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* New Session Button */}
        <button
          onClick={onNewSession}
          className="px-3 py-1 text-xs cursor-pointer transition-colors"
          style={{ backgroundColor: COLORS.brightCyan, color: COLORS.bg }}
          onMouseEnter={(e) => e.target.style.backgroundColor = COLORS.cyan}
          onMouseLeave={(e) => e.target.style.backgroundColor = COLORS.brightCyan}
        >
          + New
        </button>

        <button 
          onClick={onAboutClick}
          className="cursor-pointer"
          style={{ color: COLORS.dim }}
          onMouseEnter={(e) => e.target.style.color = COLORS.white}
          onMouseLeave={(e) => e.target.style.color = COLORS.dim}
        >
          about
        </button>
      </div>
      
      {/* Collapse/Expand buttons */}
      <div className="flex items-center gap-2">
        <button
          onClick={onCollapseAll}
          className="px-2 py-1 text-xs cursor-pointer"
          style={{ color: COLORS.dim }}
          onMouseEnter={(e) => e.target.style.color = COLORS.white}
          onMouseLeave={(e) => e.target.style.color = COLORS.dim}
        >
          ▶ collapse
        </button>
        <button
          onClick={onExpandAll}
          className="px-2 py-1 text-xs cursor-pointer"
          style={{ color: COLORS.dim }}
          onMouseEnter={(e) => e.target.style.color = COLORS.white}
          onMouseLeave={(e) => e.target.style.color = COLORS.dim}
        >
          ▼ expand
        </button>
      </div>
    </div>
  );
};

// Status Bar Component
const StatusBar = ({ stats }) => {
  return (
    <div className="flex items-center justify-between px-4 py-1 text-xs"
         style={{ backgroundColor: COLORS.bgSecondary }}>
      <div className="flex items-center gap-4">
        <div>
          <span style={{ color: stats.isRunning ? COLORS.brightGreen : COLORS.dim }}>●</span>
          <span className="ml-1" style={{ color: COLORS.dim }}>
            {stats.isRunning ? 'running' : 'idle'}
          </span>
        </div>
        <div style={{ color: COLORS.dim }}>
          turn <span style={{ color: COLORS.white }}>{stats.turn}</span>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <div style={{ color: COLORS.dim }}>
          tokens: <span style={{ color: COLORS.brightYellow }}>{stats.tokensIn.toLocaleString()}</span>
          <span style={{ color: COLORS.dim }}> in </span>
          <span style={{ color: COLORS.brightCyan }}>{stats.tokensOut.toLocaleString()}</span>
          <span style={{ color: COLORS.dim }}> out</span>
        </div>
        <div style={{ color: COLORS.dim }}>
          ctx: <span style={{ color: stats.contextPercent > 80 ? COLORS.brightRed : COLORS.brightGreen }}>
            {stats.contextPercent}%
          </span>
        </div>
        <div style={{ color: COLORS.dim }}>
          cost: <span style={{ color: COLORS.brightMagenta }}>${stats.cost.toFixed(3)}</span>
        </div>
        <div style={{ color: COLORS.dim }}>
          <span style={{ color: COLORS.white }}>{stats.elapsed}</span>
        </div>
      </div>
    </div>
  );
};

// Collapsible Action Group Component
const CollapsibleActionGroup = ({ group, isCollapsed, onToggle, isActive }) => {
  const dimStyle = !isActive ? { opacity: 0.7 } : {};
  const { action, children, hasIssue } = group;
  const { index, tool, command, file_path, preview } = action.content;
  
  // Get the permission result if any
  const permissionChild = children.find(c => c.msg.type === 'permission');
  const permissionStatus = permissionChild?.msg.content.action;

  if (isCollapsed) {
    // Collapsed view - single line summary
    return (
      <div 
        className="my-1 text-sm cursor-pointer flex items-center gap-1 hover:opacity-100 transition-opacity"
        style={{ ...dimStyle }}
        onClick={onToggle}
      >
        <span style={{ color: COLORS.dim, fontSize: '10px' }}>▶</span>
        <span style={{ color: COLORS.green }}>⊙</span>
        <span style={{ color: COLORS.dim }}>[{index}]</span>
        <span style={{ color: COLORS.brightBlue }}>{tool}</span>
        <span className="truncate flex-1" style={{ color: COLORS.dim }}>
          {command || file_path || ''}
        </span>
        {permissionStatus && (
          <span style={{ color: permissionStatus === 'allow' ? COLORS.green : COLORS.red }}>
            {permissionStatus === 'allow' ? '✓' : '×'}
          </span>
        )}
      </div>
    );
  }

  // Expanded view
  return (
    <div style={dimStyle}>
      <div 
        className="my-1 text-sm cursor-pointer flex items-center gap-1"
        onClick={onToggle}
      >
        <span style={{ color: COLORS.dim, fontSize: '10px' }}>▼</span>
        <span style={{ color: COLORS.green }}>⊙</span>
        <span style={{ color: COLORS.dim }}>[{index}]</span>
        <span style={{ color: COLORS.brightBlue }}>{tool}</span>
        {command && <span style={{ color: COLORS.white }}>{command}</span>}
        {file_path && <span style={{ color: COLORS.white }}>{file_path}</span>}
      </div>
      {preview && (
        <pre className="ml-6 mt-1 text-xs" style={{ color: COLORS.dim }}>{preview}</pre>
      )}
      {children.map(({ msg, idx }) => (
        <MessageRenderer key={idx} message={msg} isActive={isActive} isLatest={false} />
      ))}
    </div>
  );
};

// About Modal Component
const AboutModal = ({ isOpen, onClose }) => {
  if (!isOpen) return null;
  
  return (
    <div className="fixed inset-0 flex items-center justify-center z-50" 
         style={{ backgroundColor: 'rgba(0,0,0,0.8)' }}
         onClick={onClose}>
      <div className="p-6 max-w-md mx-4" style={{ backgroundColor: COLORS.bgSecondary }}>
        <h2 className="text-lg mb-4" style={{ color: COLORS.brightCyan }}>About Agent Terminal</h2>
        <p className="text-sm mb-4" style={{ color: COLORS.white }}>
          A terminal interface for agentic AI workflows.
        </p>
        <p className="text-sm mb-4" style={{ color: COLORS.dim }}>Version 1.0.0</p>
        <button 
          onClick={onClose}
          className="px-4 py-2 text-sm"
          style={{ backgroundColor: COLORS.bg, color: COLORS.white }}>
          Close
        </button>
      </div>
    </div>
  );
};

// Main Terminal Component
export default function TerminalAgent() {
  const [messages, setMessages] = useState(sampleMessages);
  const [inputValue, setInputValue] = useState('');
  const [splitPosition, setSplitPosition] = useState(75);
  const [isDragging, setIsDragging] = useState(false);
  const [isAboutOpen, setIsAboutOpen] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState('71132667-30ed-4840-a576-a443e622b58f');
  const [sessions, setSessions] = useState(sessionHistory);
  const [collapsedGroups, setCollapsedGroups] = useState(new Set());
  const [stats, setStats] = useState({
    isRunning: false,
    turn: 5,
    tokensIn: 12847,
    tokensOut: 3421,
    contextPercent: 34,
    cost: 0.082,
    elapsed: '24.3s'
  });

  // Group messages into collapsible action groups
  const getMessageGroups = useCallback(() => {
    const groups = [];
    let currentGroup = null;

    messages.forEach((msg, idx) => {
      if (msg.type === 'action') {
        // Start a new action group
        if (currentGroup) {
          groups.push(currentGroup);
        }
        currentGroup = {
          id: `action-${msg.content.index}`,
          action: msg,
          actionIndex: idx,
          children: [],
          hasIssue: false,
        };
      } else if (msg.type === 'permission' && currentGroup) {
        currentGroup.children.push({ msg, idx });
        if (msg.content.action === 'deny') {
          currentGroup.hasIssue = true;
        }
        // Close the group after permission
        groups.push(currentGroup);
        currentGroup = null;
      } else if (msg.type === 'error' && currentGroup) {
        currentGroup.children.push({ msg, idx });
        currentGroup.hasIssue = true;
        groups.push(currentGroup);
        currentGroup = null;
      } else {
        // Standalone message (including thinking, task, result, etc.)
        if (currentGroup) {
          groups.push(currentGroup);
          currentGroup = null;
        }
        groups.push({ id: `msg-${idx}`, standalone: msg, standaloneIndex: idx });
      }
    });

    if (currentGroup) {
      groups.push(currentGroup);
    }

    return groups;
  }, [messages]);

  // Auto-collapse previous successful groups when new task starts
  useEffect(() => {
    const groups = getMessageGroups();
    const newCollapsed = new Set();
    
    // Find the last task/user-input index
    let lastTaskIndex = -1;
    messages.forEach((msg, idx) => {
      if (msg.type === 'task' || msg.type === 'user-input') {
        lastTaskIndex = idx;
      }
    });

    // Collapse all action groups before the last task that don't have issues
    groups.forEach(group => {
      if (group.action) {
        const isBeforeLastTask = lastTaskIndex > -1 && group.actionIndex < lastTaskIndex;
        const isOldGroup = groups.indexOf(group) < groups.length - 3; // Not in last 3 groups
        
        if ((isBeforeLastTask || isOldGroup) && !group.hasIssue) {
          newCollapsed.add(group.id);
        }
      }
    });

    setCollapsedGroups(newCollapsed);
  }, [messages, getMessageGroups]);

  const toggleGroup = (groupId) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  };

  const collapseAll = () => {
    const groups = getMessageGroups();
    const allGroupIds = new Set();
    groups.forEach(group => {
      if (group.action) {
        allGroupIds.add(group.id);
      }
    });
    setCollapsedGroups(allGroupIds);
  };

  const expandAll = () => {
    setCollapsedGroups(new Set());
  };
  
  const outputRef = useRef(null);
  const containerRef = useRef(null);
  const inputRef = useRef(null);

  // Find active message index (last action or result)
  const activeIndex = messages.length - 1;

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [messages]);

  const handleMouseDown = useCallback((e) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleMouseMove = useCallback((e) => {
    if (!isDragging || !containerRef.current) return;
    const containerRect = containerRef.current.getBoundingClientRect();
    const newPosition = ((e.clientY - containerRect.top) / containerRect.height) * 100;
    setSplitPosition(Math.min(90, Math.max(30, newPosition)));
  }, [isDragging]);

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  useEffect(() => {
    if (isDragging) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [isDragging, handleMouseMove, handleMouseUp]);

  const handleTouchMove = useCallback((e) => {
    if (!isDragging || !containerRef.current) return;
    const touch = e.touches[0];
    const containerRect = containerRef.current.getBoundingClientRect();
    const newPosition = ((touch.clientY - containerRect.top) / containerRect.height) * 100;
    setSplitPosition(Math.min(90, Math.max(30, newPosition)));
  }, [isDragging]);

  useEffect(() => {
    if (isDragging) {
      window.addEventListener('touchmove', handleTouchMove);
      window.addEventListener('touchend', handleMouseUp);
      return () => {
        window.removeEventListener('touchmove', handleTouchMove);
        window.removeEventListener('touchend', handleMouseUp);
      };
    }
  }, [isDragging, handleTouchMove, handleMouseUp]);

  const handleSubmit = () => {
    if (!inputValue.trim() || isProcessing) return;
    
    const userMsg = { type: 'user-input', content: inputValue.trim() };
    setMessages(prev => [...prev, userMsg]);
    setInputValue('');
    setIsProcessing(true);
    setStats(s => ({ ...s, isRunning: true, turn: s.turn + 1 }));
    
    setTimeout(() => {
      const response = {
        type: 'thinking',
        content: "Processing your request. Connect your agent backend for real functionality.",
        chars: 71
      };
      setMessages(prev => [...prev, response]);
      setIsProcessing(false);
      setStats(s => ({ 
        ...s, 
        isRunning: false, 
        tokensIn: s.tokensIn + 150,
        tokensOut: s.tokensOut + 71,
        cost: s.cost + 0.002
      }));
    }, 1000);
  };

  const handleNewSession = () => {
    const newId = crypto.randomUUID();
    const newSession = {
      id: newId,
      date: new Date().toLocaleString('sv-SE').slice(0, 16).replace('T', ' '),
      task: 'New session...',
      status: 'running'
    };
    setSessions(prev => [newSession, ...prev]);
    setCurrentSessionId(newId);
    setMessages([]);
    setStats({
      isRunning: false,
      turn: 0,
      tokensIn: 0,
      tokensOut: 0,
      contextPercent: 0,
      cost: 0,
      elapsed: '0.0s'
    });
  };

  const handleSelectSession = (sessionId) => {
    setCurrentSessionId(sessionId);
    setCollapsedGroups(new Set());
    // In real app, load session messages from backend
    if (sessionId === '71132667-30ed-4840-a576-a443e622b58f') {
      setMessages(sampleMessages);
      setStats({
        isRunning: false,
        turn: 5,
        tokensIn: 12847,
        tokensOut: 3421,
        contextPercent: 34,
        cost: 0.082,
        elapsed: '24.3s'
      });
    } else {
      setMessages([{
        type: 'session-start',
        content: {
          sessionId: sessionId,
          model: 'claude-sonnet-4-5-20250929',
          workingDir: '/workspace',
          startedAt: new Date().toLocaleTimeString(),
        }
      }]);
      setStats({
        isRunning: false,
        turn: 0,
        tokensIn: 0,
        tokensOut: 0,
        contextPercent: 0,
        cost: 0,
        elapsed: '0.0s'
      });
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col font-mono text-sm overflow-hidden"
         style={{ backgroundColor: COLORS.bg, color: COLORS.white }}>
      
      <MenuBar 
        onAboutClick={() => setIsAboutOpen(true)} 
        onNewSession={handleNewSession}
        currentSessionId={currentSessionId}
        sessions={sessions}
        onSelectSession={handleSelectSession}
        onCollapseAll={collapseAll}
        onExpandAll={expandAll}
      />
      
      <div ref={containerRef} className="flex-1 flex flex-col relative overflow-hidden">
        
        {/* Output Area */}
        <div 
          ref={outputRef}
          className="overflow-auto px-4 py-3"
          style={{ height: `${splitPosition}%` }}
        >
          {messages.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <span style={{ color: COLORS.dim }}>Enter a task below to begin.</span>
            </div>
          ) : (
            getMessageGroups().map((group, groupIdx) => {
              const groups = getMessageGroups();
              const isLastGroup = groupIdx === groups.length - 1;
              
              if (group.standalone) {
                return (
                  <MessageRenderer 
                    key={group.id} 
                    message={group.standalone} 
                    isActive={group.standaloneIndex >= activeIndex - 2}
                    isLatest={group.standaloneIndex === activeIndex}
                  />
                );
              }
              
              // Action group
              return (
                <CollapsibleActionGroup
                  key={group.id}
                  group={group}
                  isCollapsed={collapsedGroups.has(group.id)}
                  onToggle={() => toggleGroup(group.id)}
                  isActive={isLastGroup || group.hasIssue}
                />
              );
            })
          )}
          
          {isProcessing && (
            <div className="flex items-center gap-2 mt-2">
              <span className="animate-pulse" style={{ color: COLORS.brightCyan }}>●</span>
              <span style={{ color: COLORS.dim }}>processing...</span>
            </div>
          )}
        </div>
        
        {/* Resize Handle */}
        <div
          className="h-1 cursor-row-resize"
          style={{ backgroundColor: COLORS.dim }}
          onMouseDown={handleMouseDown}
          onTouchStart={() => setIsDragging(true)}
        />
        
        {/* Input Area */}
        <div 
          className="flex flex-col p-3"
          style={{ height: `calc(${100 - splitPosition}% - 4px)`, backgroundColor: COLORS.bgSecondary }}
        >
          <div className="flex items-center justify-between mb-2 text-xs">
            <span style={{ color: COLORS.brightGreen }}>❯ input</span>
            <span style={{ color: COLORS.dim }}>
              {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Enter
            </span>
          </div>
          
          <div className="flex-1 flex gap-2">
            <textarea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter task..."
              disabled={isProcessing}
              className="flex-1 resize-none outline-none p-2 text-sm"
              style={{ 
                backgroundColor: COLORS.bg,
                color: COLORS.white,
                caretColor: COLORS.brightCyan,
              }}
            />
            <button
              onClick={handleSubmit}
              disabled={!inputValue.trim() || isProcessing}
              className="px-4 self-end"
              style={{ 
                backgroundColor: inputValue.trim() && !isProcessing ? COLORS.brightCyan : COLORS.dim,
                color: COLORS.bg,
                opacity: inputValue.trim() && !isProcessing ? 1 : 0.5,
              }}
            >
              {isProcessing ? '...' : '▶'}
            </button>
          </div>
        </div>
      </div>
      
      <StatusBar stats={stats} />
      <AboutModal isOpen={isAboutOpen} onClose={() => setIsAboutOpen(false)} />
    </div>
  );
}
