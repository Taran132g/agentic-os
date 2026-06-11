window.N8N_DATA = {
 "generated": "2026-06-09 22:15",
 "statsDays": 14,
 "workflows": [
  {
   "id": "sc7uLAejfaokWoy4",
   "name": "apply-jobs",
   "active": true,
   "updatedAt": "2026-06-02 19:03:23",
   "nodes": [
    {
     "parameters": {
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "0 9 * * 1-5"
        }
       ]
      }
     },
     "id": "32443dcd-dd92-4d0e-941f-89a5d4c70222",
     "name": "Schedule (weekdays 9am)",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "position": [
      260,
      200
     ],
     "disabled": true
    },
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
    "Schedule (weekdays 9am)": {
     "main": [
      [
       {
        "node": "Scout (Summer 2027 research)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    },
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
   "id": "j8UeWSB0VXfeLYbA",
   "name": "brainscan-outreach",
   "active": true,
   "updatedAt": "2026-06-05 18:41:08",
   "nodes": [
    {
     "parameters": {
      "httpMethod": "POST",
      "path": "brainscan-outreach",
      "responseMode": "onReceived",
      "options": {}
     },
     "id": "77c8a2d5-38cb-41af-9c55-b1571cc6b645",
     "name": "Webhook",
     "type": "n8n-nodes-base.webhook",
     "typeVersion": 2,
     "position": [
      240,
      200
     ],
     "webhookId": "ea62b4f0-87a6-438c-853b-04d3cb99677f"
    },
    {
     "parameters": {
      "command": "OUTREACH_GMAIL_DRAFT=1 python3 /Users/taranveersingh/agentic_os/brainscan_outreach.py"
     },
     "id": "ad32040c-740e-475d-8fc7-e7ceafd3b809",
     "name": "BrainScan outreach (manual)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      540,
      200
     ]
    },
    {
     "parameters": {
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "50 9 * * *"
        }
       ]
      }
     },
     "id": "ee75ee98-0456-4fe8-882f-9cd295a69c3d",
     "name": "Schedule (daily 9:50am)",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "position": [
      240,
      420
     ],
     "disabled": true
    },
    {
     "parameters": {
      "command": "OUTREACH_GMAIL_DRAFT=1 python3 /Users/taranveersingh/agentic_os/brainscan_outreach.py"
     },
     "id": "2a12fedb-d012-4745-acf5-1121f7f93b71",
     "name": "BrainScan outreach (batch)",
     "type": "n8n-nodes-base.executeCommand",
     "typeVersion": 1,
     "position": [
      540,
      420
     ]
    }
   ],
   "connections": {
    "Webhook": {
     "main": [
      [
       {
        "node": "BrainScan outreach (manual)",
        "type": "main",
        "index": 0
       }
      ]
     ]
    },
    "Schedule (daily 9:50am)": {
     "main": [
      [
       {
        "node": "BrainScan outreach (batch)",
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
   "updatedAt": "2026-06-01 19:35:27",
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
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "30 7 * * *"
        },
        {
         "field": "cronExpression",
         "expression": "30 18 * * *"
        }
       ]
      }
     },
     "id": "7fd559a6-79f4-457d-a760-a80df622cb8e",
     "name": "Schedule (7:30am & 6:30pm)",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "position": [
      260,
      520
     ],
     "disabled": true
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
    },
    "Schedule (7:30am & 6:30pm)": {
     "main": [
      [
       {
        "node": "Triage (read-only)",
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
   "active": true,
   "updatedAt": "2026-05-31 16:28:37",
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
  },
  {
   "id": "OB88g6GqbXRyxCSO",
   "name": "piontrix-outreach",
   "active": true,
   "updatedAt": "2026-06-10 02:14:26",
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
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "45 9 * * *"
        }
       ]
      }
     },
     "id": "e340a030-a18f-4c79-8182-24a975f8dbe9",
     "name": "Schedule (daily 9:45am)",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "position": [
      240,
      440
     ],
     "disabled": true
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
    "Schedule (daily 9:45am)": {
     "main": [
      [
       {
        "node": "Scout local leads",
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
   "id": "2cdhFWoZulg7rtu3",
   "name": "ff-daily-digest",
   "active": false,
   "updatedAt": "2026-05-31 16:15:55.866",
   "nodes": [
    {
     "parameters": {
      "rule": {
       "interval": [
        {
         "field": "cronExpression",
         "expression": "30 8 * * *"
        }
       ]
      }
     },
     "name": "Schedule",
     "type": "n8n-nodes-base.scheduleTrigger",
     "typeVersion": 1.2,
     "id": "8973a875-b512-4173-aa21-38bda6616f96",
     "position": [
      300,
      300
     ]
    },
    {
     "parameters": {
      "command": "/Users/taranveersingh/FindingFounders/backend/venv/bin/python3 /Users/taranveersingh/FindingFounders/backend/scripts/daily_digest.py"
     },
     "id": "a6fa7143-a6d4-4304-ad81-6a4e5a5b95bd",
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
  }
 ],
 "stats": {
  "apply-jobs": {
   "error": 5,
   "success": 5
  },
  "brainscan-outreach": {
   "success": 4
  },
  "email-triage": {
   "crashed": 3,
   "error": 2,
   "success": 12
  },
  "jobfill": {
   "success": 2
  },
  "morning-stack": {
   "success": 9
  },
  "piontrix-outreach": {
   "success": 9
  }
 },
 "recent": [
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-09 22:41:34.729",
   "stoppedAt": "2026-06-10 00:11:49.385"
  },
  {
   "workflow": "brainscan-outreach",
   "status": "success",
   "startedAt": "2026-06-09 14:06:06.049",
   "stoppedAt": "2026-06-09 14:06:07.301"
  },
  {
   "workflow": "piontrix-outreach",
   "status": "success",
   "startedAt": "2026-06-09 13:49:34.795",
   "stoppedAt": "2026-06-09 19:15:49.311"
  },
  {
   "workflow": "apply-jobs",
   "status": "success",
   "startedAt": "2026-06-09 13:05:17.687",
   "stoppedAt": "2026-06-09 15:09:19.461"
  },
  {
   "workflow": "morning-stack",
   "status": "success",
   "startedAt": "2026-06-09 12:03:44.745",
   "stoppedAt": "2026-06-09 12:03:45.828"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-09 11:39:59.027",
   "stoppedAt": "2026-06-09 12:03:45.828"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-08 22:37:54.627",
   "stoppedAt": "2026-06-08 23:03:50.603"
  },
  {
   "workflow": "brainscan-outreach",
   "status": "success",
   "startedAt": "2026-06-08 14:05:40.855",
   "stoppedAt": "2026-06-08 14:05:41.808"
  },
  {
   "workflow": "piontrix-outreach",
   "status": "success",
   "startedAt": "2026-06-08 13:46:45.820",
   "stoppedAt": "2026-06-08 14:30:53.027"
  },
  {
   "workflow": "apply-jobs",
   "status": "success",
   "startedAt": "2026-06-08 13:11:38.748",
   "stoppedAt": "2026-06-08 14:05:43.002"
  },
  {
   "workflow": "morning-stack",
   "status": "success",
   "startedAt": "2026-06-08 12:05:43.727",
   "stoppedAt": "2026-06-08 12:05:45.151"
  },
  {
   "workflow": "email-triage",
   "status": "success",
   "startedAt": "2026-06-08 11:30:41.996",
   "stoppedAt": "2026-06-08 13:49:15.803"
  }
 ]
};
