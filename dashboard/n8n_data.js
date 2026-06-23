window.N8N_DATA = {
 "generated": "2026-06-22 23:10",
 "statsDays": 14,
 "workflows": [
  {
   "id": "sc7uLAejfaokWoy4",
   "name": "apply-jobs",
   "active": true,
   "updatedAt": "2026-06-13 20:41:49",
   "nodes": [
    {
     "parameters": {
      "command": "python3 /Users/taranveersingh/agentic_os/job_scout.py"
     },
     "id": "fe5b3925-08a2-4e5f-89b0-21e55d290fac",
     "name": "Scout (Summer 2027 research)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      560,
      200
     ]
    },
    {
     "parameters": {
      "httpMethod": "POST",
      "path": "apply",
      "responseMode": "onReceived",
      "options": {}
     },
     "id": "29a34f3f-6edd-4d43-962e-93c9f635aae4",
     "name": "Webhook",
     "type": "n8n-nodes-base.webhook",
     "typeVersion": 2,
     "position": [
      260,
      420
     ],
     "webhookId": "7300aee5-aad9-45bb-a5e8-40c4e9feb775"
    },
    {
     "parameters": {
      "command": "python3 /Users/taranveersingh/agentic_os/fill_scouted.py"
     },
     "id": "5a9cf3e8-ff17-4944-bdf6-a2f87fefbfb2",
     "name": "Fill scouted (one by one)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      560,
      420
     ]
    }
   ],
   "connections": {
    "Webhook": {
     "main": [
      [
       {
        "node": "Fill scouted (one by one)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  },
  {
   "id": "VnJ9ugt07YaFaRbe",
   "name": "email-triage",
   "active": true,
   "updatedAt": "2026-06-13 20:39:58",
   "nodes": [
    {
     "parameters": {
      "httpMethod": "POST",
      "path": "triage",
      "responseMode": "onReceived",
      "options": {}
     },
     "id": "d243c251-2dc9-4220-997a-b5bae42216ae",
     "name": "Webhook",
     "type": "n8n-nodes-base.webhook",
     "typeVersion": 2,
     "position": [
      260,
      300
     ],
     "webhookId": "672a1b8b-8244-4779-ac21-73d2f2b68bb8"
    },
    {
     "parameters": {
      "command": "=python3 /Users/taranveersingh/agentic_os/email_triage.py \"{{ $json.body.act ? 'act' : '' }}\""
     },
     "id": "515b3004-31e7-42d5-9349-3dd6564ede99",
     "name": "Triage (manual)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      560,
      300
     ]
    },
    {
     "parameters": {
      "command": "python3 /Users/taranveersingh/agentic_os/email_triage.py"
     },
     "id": "3d0d0d4f-4e7e-4950-b3b0-8f4b6674c59d",
     "name": "Triage (read-only)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      560,
      520
     ]
    }
   ],
   "connections": {
    "Webhook": {
     "main": [
      [
       {
        "node": "Triage (manual)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  },
  {
   "id": "OB88g6GqbXRyxCSO",
   "name": "piontrix-outreach",
   "active": true,
   "updatedAt": "2026-06-13 20:39:58",
   "nodes": [
    {
     "parameters": {
      "httpMethod": "POST",
      "path": "outreach",
      "responseMode": "onReceived",
      "options": {}
     },
     "id": "b11da03d-6196-47a3-a249-935e0f13cab7",
     "name": "Webhook",
     "type": "n8n-nodes-base.webhook",
     "typeVersion": 2,
     "position": [
      240,
      200
     ],
     "webhookId": "921a73c1-a5c5-4fe7-bfe0-95b681baf5cd"
    },
    {
     "parameters": {
      "command": "=python3 /Users/taranveersingh/agentic_os/piontrix_outreach.py \"{{ ($json.body.business || '').toString().replace(/[^\\w\\s.,'&@#~-]/g, '') }}\" \"{{ ($json.body.website || '').toString().replace(/[^\\w.:\\/?&=%~-]/g, '') }}\" \"{{ $json.body.send ? 'send' : 'review' }}\""
     },
     "id": "e13aee87-0b8b-48c7-b54e-b9881ebfad44",
     "name": "Outreach (single)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      540,
      200
     ]
    },
    {
     "parameters": {
      "command": "python3 /Users/taranveersingh/agentic_os/piontrix_scout.py"
     },
     "id": "f289ddf7-d0f1-4f6a-8256-55ec87e57a99",
     "name": "Scout local leads",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      540,
      440
     ]
    },
    {
     "parameters": {
      "command": "OUTREACH_GMAIL_DRAFT=1 python3 /Users/taranveersingh/agentic_os/piontrix_outreach.py --batch"
     },
     "id": "37def785-0446-496b-9f64-28d24c2f8d52",
     "name": "Draft outreach (batch)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      840,
      440
     ]
    }
   ],
   "connections": {
    "Webhook": {
     "main": [
      [
       {
        "node": "Outreach (single)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    },
    "Scout local leads": {
     "main": [
      [
       {
        "node": "Draft outreach (batch)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  },
  {
   "id": "xdBliXCTgSZ41Wuf",
   "name": "content-rotation",
   "active": false,
   "updatedAt": "2026-05-31 16:15:55.864",
   "nodes": [
    {
     "parameters": {
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "0 11 * * *"
        }
       ]
      }
     },
     "name": "Schedule",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "id": "69393f83-5249-4d22-be43-45d5c244a93b",
     "position": [
      300,
      300
     ]
    },
    {
     "parameters": {
      "command": "python3 /Users/taranveersingh/agentic_os/content_cron.py"
     },
     "id": "ad9a3dbf-d0ae-421b-843e-fdc16d704ffa",
     "name": "Execute Command",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      600,
      300
     ]
    }
   ],
   "connections": {
    "Schedule": {
     "main": [
      [
       {
        "node": "Execute Command",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  },
  {
   "id": "d6ef36407e404c29",
   "name": "jobfill",
   "active": false,
   "updatedAt": "2026-06-10 02:14:26",
   "nodes": [
    {
     "parameters": {
      "httpMethod": "POST",
      "path": "jobfill",
      "responseMode": "onReceived",
      "options": {}
     },
     "id": "d7416e2d-e1da-4d21-bbc6-54f00a733615",
     "name": "Webhook",
     "type": "n8n-nodes-base.webhook",
     "typeVersion": 2,
     "position": [
      300,
      300
     ],
     "webhookId": "f927bfe9-fa01-45e0-9df9-e804e8b9a76f"
    },
    {
     "parameters": {
      "command": "=python3 /Users/taranveersingh/agentic_os/jobfill_cli.py \"{{ ($json.body.text || '').toString().replace(/[^\\w\\s|.,:\\/?&=%+@#~-]/g, '') }}\""
     },
     "id": "8499157e-10a6-41eb-9403-1d0010f8eb3c",
     "name": "Execute Command",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      560,
      300
     ]
    }
   ],
   "connections": {
    "Webhook": {
     "main": [
      [
       {
        "node": "Execute Command",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  },
  {
   "id": "ubt10GEfPE8so8SB",
   "name": "morning-stack",
   "active": false,
   "updatedAt": "2026-06-13 21:57:47",
   "nodes": [
    {
     "parameters": {
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "30 7 * * *"
        }
       ]
      }
     },
     "name": "Schedule",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "id": "75d146d9-08a2-4612-8f78-75faf4b90829",
     "position": [
      300,
      300
     ]
    },
    {
     "parameters": {
      "command": "bash /Users/taranveersingh/agentic_os/morning_stack.sh"
     },
     "id": "0a629f3b-0d34-45ec-8b30-26d285dea5e0",
     "name": "Execute Command",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      600,
      300
     ]
    }
   ],
   "connections": {
    "Schedule": {
     "main": [
      [
       {
        "node": "Execute Command",
        "type": "main",
        "index": 0
       }
      ]
     ]
    }
   }
  }
 ],
 "stats": {
  "apply-jobs": {
   "success": 6
  },
  "email-triage": {
   "success": 9
  },
  "morning-stack": {
   "success": 5
  },
  "piontrix-outreach": {
   "success": 5
  }
 },
 "recent": [
  {
   "workflow": "piontrix-outreach",
   "status": "success",
   "startedAt": "2026-06-13 13:45:01.074",
   "stoppedAt": "2026-06-13 13:51:04.249"
  },
  {
   "workflow": "morning-stack",
   "status": "success",
   "startedAt": "2026-06-13 12:00:10.043",
   "stoppedAt": "2026-06-13 12:00:28.721"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-13 11:44:07.897",
   "stoppedAt": "2026-06-13 11:44:33.910"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-12 22:30:00.574",
   "stoppedAt": "2026-06-12 22:30:50.602"
  },
  {
   "workflow": "piontrix-outreach",
   "status": "success",
   "startedAt": "2026-06-12 13:45:00.072",
   "stoppedAt": "2026-06-12 13:47:37.415"
  },
  {
   "workflow": "apply-jobs",
   "status": "success",
   "startedAt": "2026-06-12 13:00:00.074",
   "stoppedAt": "2026-06-12 13:08:57.045"
  },
  {
   "workflow": "morning-stack",
   "status": "success",
   "startedAt": "2026-06-12 12:06:31.806",
   "stoppedAt": "2026-06-12 12:06:32.821"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-12 11:40:27.783",
   "stoppedAt": "2026-06-12 12:40:51.255"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-11 22:30:01.105",
   "stoppedAt": "2026-06-11 22:30:46.024"
  },
  {
   "workflow": "apply-jobs",
   "status": "success",
   "startedAt": "2026-06-11 21:19:03.282",
   "stoppedAt": "2026-06-11 21:19:50.179"
  },
  {
   "workflow": "piontrix-outreach",
   "status": "success",
   "startedAt": "2026-06-11 13:47:03.787",
   "stoppedAt": "2026-06-11 13:54:19.984"
  },
  {
   "workflow": "apply-jobs",
   "status": "success",
   "startedAt": "2026-06-11 13:16:40.804",
   "stoppedAt": "2026-06-11 13:59:02.206"
  }
 ]
};
