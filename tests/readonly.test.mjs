import test from 'node:test';
import assert from 'node:assert/strict';
import { readOnlyTransport } from '../dist/readonly-transport.js';
import { createReadOnlyServer } from '../dist/server.js';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
const expected = ['get_devices','get_top_talkers','get_clients_by_network','get_offline_devices','get_device_flows','search_flows','get_dns_queries','get_alarms','get_audit_logs','get_rules','get_target_lists','get_features','get_network_status','get_network_stats','get_network_performance','get_wan_usage','get_vlans','get_system_info','get_vpn_status'].sort();
test('server advertises only reviewed reads, rejects writes, and performs a bounded system read', async () => {
  let reads = 0;
  const server = createReadOnlyServer({getInit: async () => { reads++; return {model:'synthetic-box',version:'fixture'}; }});
  const client = new Client({name:'fixture',version:'1'});
  const [left,right] = InMemoryTransport.createLinkedPair();
  await server.connect(readOnlyTransport(left));
  await client.connect(right);
  try {
    const list = await client.listTools();
    assert.deepEqual(list.tools.map(t=>t.name).sort(), expected);
    assert.ok(list.tools.every(t=>t.annotations.readOnlyHint===true && t.annotations.destructiveHint===false));
    await assert.rejects(client.callTool({name:'delete_rule',arguments:{}}), error=>error.code===-32601);
    assert.equal(reads,0);
    const result = await client.callTool({name:'get_system_info',arguments:{}});
    assert.notEqual(result.isError,true);
    assert.equal(JSON.parse(result.content[0].text).model,'synthetic-box');
    assert.equal(reads,1);
  } finally { await client.close(); await server.close(); }
});

test('paired group selection is exact and missing identity cannot fall back to the first box', async () => {
  const { FirewallaClient } = await import('../dist/firewalla-client.js');
  const { FWGroupApi, SecureUtil } = await import('node-firewalla');
  const originalLogin=FWGroupApi.login;
  const originalImport=SecureUtil.importKeyPair;
  const originalDecrypt=SecureUtil.rsaDecrypt;
  const originalSend=FWGroupApi.sendMessageToBox;
  let logins=0;
  FWGroupApi.login=async()=>{logins++;return {groups:[{_id:'wrong',eid:'fixture',aid:'fixture',symmetricKeys:[{key:'fixture'}]},{_id:'selected',eid:'fixture',aid:'fixture',symmetricKeys:[{key:'fixture'}]}]};};
  SecureUtil.importKeyPair=()=>{};
  SecureUtil.rsaDecrypt=()=> 'fixture';
  try {
    const client=new FirewallaClient('192.0.2.1','/fixture/public','/fixture/private','selected');
    await client.connect();
    assert.equal(client.fwGroup.gid,'selected');
    await assert.rejects(new FirewallaClient('192.0.2.1','/fixture/public','/fixture/private','missing').connect(),/Selected paired group unavailable/);
    assert.equal(logins,2);
  } finally {
    FWGroupApi.login=originalLogin;
    SecureUtil.importKeyPair=originalImport;
    SecureUtil.rsaDecrypt=originalDecrypt;
    FWGroupApi.sendMessageToBox=originalSend;
  }
});
