# LABORATÓRIO MINICURSO SBRC 2026   
 --- 
## Visão Geral   
Este tutorial descreve a implantação de uma aplicação leve em **Python/Flask** que atua como a **KME (Key Management Entity - Entidade de Gerenciamento de Chaves)**.   
A aplicação implementa integralmente a API de entrega de chaves REST conforme a norma **ETSI GS QKD 014 **e autentica todos os clientes por meio de **TLS Mútuo (mTLS)**.   
> ⚠️ Aviso: Este tutorial foi projetado para um laboratório isolado no GNS3. A geração de chaves via módulo randomnão é criptograficamente segura e nunca deve ser utilizada em ambiente de produção.   

## Topologia de Rede   
O cenário consiste em dois roteadores MikroTik e um único servidor KME centralizado.   
Plaintext   
```
      NAT1                                    NAT2
   (192.168.122.1)                        (192.168.122.1)
        |nat0                                  |nat0
        |ether3                                |ether3
  MikroTikCHR-1  ←——ether1——ether1——→  MikroTikCHR-2
  (192.168.122.10)  10.0.0.1/30        (192.168.122.20)
  (10.0.0.1)        10.0.0.2/30          (10.0.0.2)
  (172.16.1.1)                           (172.16.2.1)
        |ether2                                |ether2
        |eth0                                  |eth1
        +——————————KME-sim——————————+
                  (172.16.1.10)  eth0  → CHR-1 LAN
                  (172.16.2.10)  eth1  → CHR-2 LAN


```
### Endereçamento de IP   
|   **Dispositivo** | **Interface** |  **Endereço IP** |  **Máscara / CIDR** |      **Gateway / Observação** |
|:------------------|:--------------|:-----------------|:--------------------|:------------------------------|
|          **NAT1** |          nat0 |    192.168.122.1 |                 /24 |        Host (Gateway Externo) |
| **MikroTikCHR-1** |        ether3 |   192.168.122.10 |                 /24 |                  Conexão NAT1 |
| **MikroTikCHR-1** |        ether1 |         10.0.0.1 |                 /30 | Link Ponto-a-Ponto (Backbone) |
| **MikroTikCHR-1** |        ether2 |       172.16.1.1 |                 /24 |            Gateway LAN Site 1 |
| **MikroTikCHR-2** |        ether3 |   192.168.122.20 |                 /24 |                  Conexão NAT2 |
| **MikroTikCHR-2** |        ether1 |         10.0.0.2 |                 /30 | Link Ponto-a-Ponto (Backbone) |
| **MikroTikCHR-2** |        ether2 |       172.16.2.1 |                 /24 |            Gateway LAN Site 2 |
|       **KME-sim** |          eth0 |      172.16.1.10 |                 /24 |           Gateway: 172.16.1.1 |
|       **KME-sim** |          eth1 |      172.16.2.10 |                 /24 |           Gateway: 172.16.2.1 |

## Decisões Arquiteturais   
- **KME Único:** Existe apenas um servidor KME com duas interfaces de rede (uma para cada LAN dos roteadores).   
- **Protocolo:** O KME serve **HTTPS com TLS mútuo** na porta **8020** em todas as interfaces.   
- **Persistência:** Chaves geradas sob demanda e armazenadas em SQLite.   
- **Conexões SAE:** O **CHR-1** conecta-se como `sae-chr1` e o **CHR-2** como `sae-chr2`.   
- **IPsec:** O túnel é estabelecido sobre a rede `10.0.0.0/30`.   
- **Requisito de Software:** RouterOS **v7.22** ou superior (QKD foi incluído em v7.21 - [https://help.mikrotik.com/docs/spaces/ROS/pages/341770268/QKD](https://help.mikrotik.com/docs/spaces/ROS/pages/341770268/QKD))..   
 --- 
   
## Passo 0: Instalação de Requisitos   
Instale o Docker e o servidor GNS3 em sua máquina host:   
- Instalar docker na máquina ([https://www.docker.com/get-started/](https://www.docker.com/get-started/))   
- Instalar gns3-server no docker hub ([https://hub.docker.com/r/gns3/gns3-server](https://hub.docker.com/r/gns3/gns3-server))   
   
Bash   
```
sudo systemctl start docker.service
sudo systemctl enable docker.service

```
 --- 
## Passo 1: Customização da Imagem GNS3 Server   
A imagem oficial do GNS3 não possui o `busybox`, necessário para inicializar nós Docker no ambiente.   
1. **Criar o Dockerfile:**   
   
```
mkdir ~/gns3_dockerfile
cd ~/gns3_dockerfile && nano Dockerfile.gns3

```
Conteúdo do `Dockerfile.gns3`:   
```
FROM gns3/gns3-server
USER root
RUN apt-get update && \
    apt-get install -y busybox-static && \
    ln -sf /bin/busybox /usr/local/bin/busybox && \
    rm -rf /var/lib/apt/lists/*

```
1. **Buildar a imagem:**   
   
```
docker build -f Dockerfile.gns3 -t gns3-server-custom .

```
 --- 
## Passo 2: Geração de Certificados   
Como há apenas um KME, utilizaremos uma única CA para assinar todos os certificados. Execute os comandos no host (fora do container).   
```
mkdir -p ~/qkd-lab-single/certs && cd ~/qkd-lab-single/certs

```
- **Etapa A — Root CA:**   
   
```
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=QKD-Lab-CA"

```
- **Etapa B — Certificado do Servidor KME (com SubjectAltName):**   
   
> ⚠️ Importante: O RouterOS valida estritamente o IP no certificado. Ambos os IPs do KME devem estar no SAN.   

```
cat > kme-server.ext << 'EOF'
subjectAltName = IP:172.16.1.10, IP:172.16.2.10
EOF

openssl genrsa -out kme-server.key 4096
openssl req -new -key kme-server.key -out kme-server.csr -subj "/CN=kme1"
openssl x509 -req -days 3650 -in kme-server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out kme-server.crt -extfile kme-server.ext

```
- **Etapa C e D — Certificados das SAEs (CHR-1 e CHR-2):**   
   
Repita o processo para `sae-chr1` e *`sae-chr2` conforme os comandos originais.*   
```
openssl genrsa -out sae-chr1.key 4096

openssl req -new -key sae-chr1.key -out sae-chr1.csr -subj "/CN=sae-chr1"

openssl x509 -req -days 3650-in sae-chr1.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out sae-chr1.crt

```
```
openssl genrsa -out sae-chr2.key 4096

openssl req -new -key sae-chr2.key -out sae-chr2.csr -subj "/CN=sae-chr2"

openssl x509 -req -days 3650-in sae-chr2.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out sae-chr2.crt

```
   
Verifique que todos os arquivos foram criado:   
```
ls ~/qkd-lab-single/certs/
# ca.key ca.crt kme-server.key kme-server.crt kme.ext
# sae-chr1.key sae-chr1.crt sae-chr2.key sae-chr2.crt

```
 --- 
## Passo 3: Aplicação KME e Configuração Docker   
### Código Python/Flask (kme\_server.py)   
A aplicação implementa os endpoints ETSI QKD:   
- `GET /status`: Verifica chaves armazenadas.   
- `POST /enc\_keys`: Master SAE solicita novas chaves.   
- `POST /dec\_keys`: Slave SAE recupera chaves por ID.   
   
(O código Python permanece o mesmo, tratando o CN do certificado cliente como a identidade da SAE via *`SSL\_CLIENT\_CN`).*   
   
Crie o `kme\_server.py` no diretório `~/qkd-lab-single`:   
> Copie o arquivo disponibilizado (ou faça um!)   


### Dockerfile do KME   
Crie o `Dockerfile` no diretório `~/qkd-lab-single`:   
```
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y python3 python3-pip openssl sqlite3 iproute2 iputils-ping && rm -rf /var/lib/apt/lists/*
RUN pip3 install flask --break-system-packages
RUN mkdir -p /opt/kme/certs
COPY kme_server.py /opt/kme/kme_server.py
COPY certs/ca.crt /opt/kme/certs/ca.crt
COPY certs/kme-server.crt /opt/kme/certs/kme-server.crt
COPY certs/kme-server.key /opt/kme/certs/kme-server.key
EXPOSE 8020
CMD ["python3", "/opt/kme/kme_server.py"]

```
   
```
cd ~/qkd-lab-single
docker build -t qkd-kme-sim:latest .
```
 --- 
## Passo 4   
O GNS3 monta um script `init.sh` de dentro do contêiner GNS3 no contêiner da sua aplicação em tempo de execução. Como o daemon Docker do host resolve todos os caminhos de montagem em relação ao sistema de arquivos do host, o caminho interno do contêiner GNS3 deve existir de forma idêntica no host.   
### Preparar diretórios no host   
```
mkdir -p /root/.local/share/GNS3/docker
mkdir -p $HOME/GNS3/projects
mkdir -p $HOME/GNS3/symbols
```
> Nota: O caminho /root/.local/share/GNS3/docker deve ser idêntico dentro do contêiner e no host. Este é o requisito central de resolução de caminhos no Docker-dentro-do-Docker.   

 --- 
## Passo 5: Configurar nó NAT Para Acesso a Internet   
No host:   
   
Ubuntu   
```
sudo apt-get install libvirt bridge-utils dnsmasq ebtables iptables-nft
```
Arch   
```
sudo pacman -S libvirt bridge-utils dnsmasq ebtables iptables-nft

```
   
```

# Start and enable the libvirt daemon
sudo systemctl enable --now libvirtd

# Start the 'default' NAT network
sudo virsh net-start default
sudo virsh net-autostart default

# Libera portas necessárias para acesso a internet
sudo iptables -I FORWARD -p udp --dport 53 -j ACCEPT
sudo iptables -I FORWARD -p tcp --dport 53 -j ACCEPT
```
 --- 
## Passo 6: Inicializar Servidor GNS3 (Docker)   
Execute o comando `docker run` com as flags de privilégio necessárias para que o uBridge gerencie as interfaces de rede.   
```
docker run -d \
  --name gns3-server \
  --privileged \
  --net=host \
  --pid=host \
  --restart always \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  --cap-add=SYS_ADMIN \
  --cap-add=SYS_PTRACE \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /run:/run \
  -v /dev:/dev \
  -v $HOME/GNS3/projects:/root/GNS3/projects \
  -v $HOME/GNS3/symbols:/root/GNS3/symbols \
  -v /root/.local/share/GNS3/docker:/root/.local/share/GNS3/docker \
  gns3-server-custom


```
   
### Referência das Flags   
|                          Flag |                                                                                                                                 Utilidade |
|:------------------------------|:------------------------------------------------------------------------------------------------------------------------------------------|
|                `--privileged` |                                                                                Necessário para o uBridge criar e gerenciar interfaces TAP |
|                  `--net=host` |                                                                             O GNS3 precisa de acesso direto às interfaces de rede do host |
|                  `--pid=host` |    **Crítico:** compartilha o namespace de PID do host para que o uBridge resolva `/proc/<PID>/ns/net` nas operações de namespace de rede |
|    `--cap-add=NET\_ADMIN/RAW` |                                                                                       Permite criação e manipulação de interfaces de rede |
| `--cap-add=SYS\_ADMIN/PTRACE` |                                                                                         Necessário para operações de namespace e processo |
|              `-v docker.sock` |                                                                        Permite ao GNS3 criar contêineres irmãos via daemon Docker do host |
|                `-v /run:/run` |                                                                             Expõe os arquivos de namespace de rede do host para o uBridge |
|                `-v /dev:/dev` |                                                                           Necessário para acesso a dispositivos pelas ferramentas de rede |
|              `-v GNS3/docker` |                Caminho idêntico no host e no contêiner para que o daemon Docker resolva corretamente os caminhos de montagem do `init.sh` |

### GNS3 Web UI onine!! (http://localhost:3080)   
   
Após iniciar o contêiner, execute estas verificações antes de adicionar nós no GNS3.   
   
**Verifique se o busybox está presente no contêiner GNS3:**   
```
docker exec gns3-server which busybox
docker exec gns3-server busybox --version

```
   
Verifique se o **`init.sh` foi gerado:**   
```
ls -la /root/.local/share/GNS3/docker/

```
Você deve ver o `init.sh` e outros arquivos de bootstrap populados pelo GNS3 na primeira inicialização.   
   
**Verifique se o namespace de PID está compartilhado com o host:**   
```
docker exec gns3-server ps aux | head -5

```
Você deve ver processos do host como `systemd` e threads do kernel (PID 1 = systemd, não GNS3).   
   
**Verifique os logs do servidor GNS3:**   
```
docker logs gns3-server 2>&1 | tail -30


```
 --- 
## Passo 7: Adicionar KME ao Ambiente GNS3   
Na interface Web do GNS3 em `http://localhost:3080`:   
1. Acesse **Preferences > Docker containers > New.**   
2. Insira o nome exato da imagem conforme exibido em `docker images` (ex.: qkd-kme-sim:latest) e nomeie KME.   
3. Defina o número de adaptadores de rede conforme necessário.   
4. Deixe o comando de inicialização em branco.   
5. Salve o template e arraste-o para o canvas do seu projeto.   
   
> Atenção: certifique-se de que o nome da imagem no template do GNS3 corresponde exatamente à tag exibida em docker images. Uma divergência fará com que o GNS3 tente baixar a imagem ou falhe silenciosamente   

   
Após inserido no ambiente, pode ser necessário configurar seu endereço IPv4 diretamente no docker   
## Passo 8: Adicionar MikroTik CHR ao Ambiente GNS3   
Baixar appliance de roteador mikrotik ([https://gns3.com/marketplace/appliances/mikrotik-cloud-hosted-router](https://gns3.com/marketplace/appliances/mikrotik-cloud-hosted-router)) junto com a versão 7.16 (documentação - [https://help.mikrotik.com/docs/](https://help.mikrotik.com/docs/))   
> Observação: depois atualizaremos para versão necessária no passo 7.4.    

Na web UI:    
1. Add black project   
2. +   
3. Create new template   
4. Import an appliance file   
5. selecionar arquivo .gns3a e a versão instaladas   
   
   
Acessar roteador com web console   
- user: admin (senha inicial vazia, dps pede pra alterar)   
   
   
Configurar o ip dos roteadores   
Os roteadores devem estar na mesma sub-rede (ex.: 10.2.1.x) e rot1 deve estar na mesma sub-rede que o nó NAT (é possível verificar utilizando o comando "ip a" no terminal linux e observar qual ip está alocado para a interface "virbri0", no exemplo o ip desta interface é 192.168.122.1)   
   
   
![image_1774294643220_0](files/image_1774294643220_0.png)    
   
### Considerações Técnicas do Projeto   
- **Segmentação de Backbone:** Utilizei a rede `10.0.0.0/30` para a interconexão entre os roteadores MikroTik. O uso de máscaras `/30` é uma prática recomendada para links ponto-a-ponto, pois minimiza o desperdício de endereços.   
- **Redes de Dados (KME):** As redes `172.16.1.0/24` e `172.16.2.0/24` garantem isolamento para as instâncias de simulação, permitindo testes de roteamento estático ou dinâmico (OSPF/BGP) entre os sites.   
- **Link Direto Inter-KME:** A conexão entre `eth1` dos containers simula um canal fora-de-banda ou um link dedicado, configurado na rede `10.0.1.0/30`.   
- **Acesso Externo:** Os IPs `.10` e `.20` na rede `192.168.122.0` foram escolhidos para evitar conflitos com o DHCP padrão do libvirt/GNS3, que geralmente inicia em faixas mais baixas.   
 --- 
   
## Passo 9: Configuração dos MikroTik CHRs   
Após importar o appliance no GNS3 e atualizar para a versão **7.22+**, configure os IPs e rotas.   
### 9.1 - MikroTikCHR-1   
Conecte-se a CHR-1 via GNS3 web console:   
```
/ip address add address=192.168.122.10/24 interface=ether3
/ip address add address=10.0.0.1/30 interface=ether1
/ip address add address=172.16.1.1/24 interface=ether2

/ip route add dst-address=0.0.0.0/0 gateway=192.168.122.1
/ip route add dst-address=172.16.2.0/24 gateway=10.0.0.2

/ip dns set servers=8.8.8.8

```
### 9.2 - MikroTikCHR-2   
Conecte-se a CHR-2 via GNS3 web console:   
```
/ip address add address=192.168.122.20/24 interface=ether3
/ip address add address=10.0.0.2/30 interface=ether1
/ip address add address=172.16.2.1/24 interface=ether2

/ip route add dst-address=0.0.0.0/0 gateway=192.168.122.1
/ip route add dst-address=172.16.1.0/24 gateway=10.0.0.1

/ip dns set servers=8.8.8.8

```
### 9.3 - Verifique as conexões   
**CHR-1**:   
```
/ping 10.0.0.2 count=3
/ping 192.168.122.1 count=3

```
**CHR-2**:   
```
/ping 10.0.0.1 count=3
/ping 192.168.122.1 count=3

```
> ⚠️ Todos as conexões devem estar ativas para prosseguir.   

   
### 9.4 - Atualizar RouterOS   
No navegador:    
1. Acesse o roteador no endereço especificado em ether3 (192.168.122.x)   
2. Faça o login   
3. Menu superior direito: *Advanced*   
4. Menu lateral esquerdo: *Files*   
5. Adicione o arquivo .npk da versão atualizada do RouterOS (qualquer versão acima da 7.22 disponível em: [https://mikrotik.com/download?v=7.22&architecture=x86](https://mikrotik.com/download?v=7.22&architecture=x86))   
   
   
No GNS3 Web UI:   
- Verifique em ambos os roteadores que o arquivo está lá   
   
```
/file print

```
- Reinicia o roteador   
   
```
/system reboot

```
   
Aguarde alguns segundos e verá a versão sendo atualizada no terminal. Faça o login novamente.   
 --- 
## Passo 10: Configurar Endereços IP do KME    
No terminal do host   
```
docker ps -a
```
   
Procure pelo container KME-sim, anote o CONTAINER ID ou NOME   
```
docker exec -it <kme> bash

ip addr add 172.16.1.10/24 dev eth0
ip link set eth0 up
ip addr add 172.16.2.10/24 dev eth1
ip link set eth1 up
ip route add default via 172.16.1.1


```
> ⚠️ Essas configurações serão resetadas ao reiniciar o docker.   

   
Certifique-se que alcançar ambos os roteadores   
```
ping 172.16.1.10 #MKT1
ping 172.16.2.10 #MKT2
```
 --- 
## Passo 11: Verificar Inicialização do Servidor KME   
```
docker exec <kme> cat /var/log/kme.log

```
Output espreado:   
```
2025-01-01 00:00:00,001 INFO Database initialised at /opt/kme/kme.db
2025-01-01 00:00:00,002 INFO KME server starting — KME_ID=kme1  DB=/opt/kme/kme.db  port=8020
 * Running on https://0.0.0.0:8020

```
 --- 
## Passo 10: Importação de Certificados nos Roteadores   
Cada roteador necessita do `ca.crt`, seu próprio certificado `.crt` e sua chave privada `.key`.   
**No Host para CHR-1:**   
```
scp ~/qkd-lab-single/certs/ca.crt admin@192.168.122.10:/
scp ~/qkd-lab-single/certs/sae-chr1.crt admin@192.168.122.10:/
scp ~/qkd-lab-single/certs/sae-chr1.key admin@192.168.122.10:/


```
**No Console do CHR-1:**   
```
/certificate import file-name=ca.crt passphrase=""
/certificate import file-name=sae-chr1.crt passphrase=""
/certificate import file-name=sae-chr1.key passphrase=""

```
   
**No Host para CHR-2:**   
```
scp ~/qkd-lab-single/certs/ca.crt admin@192.168.122.20:/
scp ~/qkd-lab-single/certs/sae-chr2.crt admin@192.168.122.20:/
scp ~/qkd-lab-single/certs/sae-chr2.key admin@192.168.122.20:/

```
**No Console do CHR-2:**   
```
/certificate import file-name=ca.crt passphrase=""
/certificate import file-name=sae-chr2.crt passphrase=""
/certificate import file-name=sae-chr2.key passphrase=""

```
 --- 
## Passo 11: Configuração QKD e IPsec   
Configure como o roteador deve buscar as chaves no servidor KME.   
### 11.1 - Configuração QKD CHR-1   
```
/ip ipsec key qkd set \
  address=172.16.1.10:8020 \
  cache-size=5 \
  certificate=sae-chr1 \
  key-size=64 \
  kme-id=kme1 \
  peer-sae-id=sae-chr2

```
|     Parameter |            Value |                                                       Explanation |
|:--------------|:-----------------|:------------------------------------------------------------------|
|     `address` | 172.16.1.10:8020 |                               IP e porta da interface eth0 do KME |
|  `cache-size` |                5 |                      Número de chaves a serem pré-buscadas do KME |
| `certificate` |         sae-chr1 | Deve ser exatamente igual ao nome presente em`/certificate print` |
|    `key-size` |               64 |                           64 bytes = 512 bits, atendendo RFC 8784 |
|      `kme-id` |             kme1 |     Deve ser igual a `KME\_ID` nas configurações do código python |
| `peer-sae-id` |         sae-chr2 |                                                 O SAE ID de CHR-2 |

Configuração IPsec com PPK (Quantum-Safe)   
```
/ip ipsec profile add name=qkd-profile ppk=qkd
/ip ipsec proposal add name=qkd-proposal enc-algorithms=aes-256-cbc auth-algorithms=sha256
/ip ipsec peer add address=10.0.0.2/32 exchange-mode=ike2 name=peer-chr2 profile=qkd-profile
/ip ipsec identity add peer=peer-chr2 auth-method=pre-shared-key secret="bbmp"
/ip ipsec policy add src-address=172.16.1.0/24 dst-address=172.16.2.0/24 peer=peer-chr2 proposal=qkd-proposal tunnel=yes


```
   
### 11.2 - Configuração QKD CHR-2   
```
/ip ipsec key qkd set \
  address=172.16.2.10:8020 \
  cache-size=5 \
  certificate=sae-chr2 \
  key-size=64 \
  kme-id=kme1 \
  peer-sae-id=sae-chr1

```
> Note que ambos os roteadores usam o mesmo kme-id (kme1) porque ambos consomem chaves do mesmo KME   

 Configuração IPsec com PPK (Quantum-Safe)   
```
/ip ipsec profile add name=qkd-profile ppk=qkd
/ip ipsec proposal add name=qkd-proposal enc-algorithms=aes-256-cbc auth-algorithms=sha256
/ip ipsec peer add address=10.0.0.2/32 exchange-mode=ike2 name=peer-chr2 profile=qkd-profile
/ip ipsec identity add peer=peer-chr2 auth-method=pre-shared-key secret="bbmp"
/ip ipsec policy add src-address=172.16.1.0/24 dst-address=172.16.2.0/24 peer=peer-chr2 proposal=qkd-proposal tunnel=yes


```
 --- 
## Passo 12: Verificação e Diagnóstico   
### Tabela de Erros Comuns   
|           **Mensagem de Log** |                                        **Causa Provável** |                                    **Correção** |
|:------------------------------|:----------------------------------------------------------|:------------------------------------------------|
| `SSL name verification error` |          SAN do certificado KME ausente ou CN ≠ `kme-id`. |     Regenere o certificado com os IPs corretos. |
|   `certificate verify failed` |   O `ca.crt` no roteador não é o mesmo que assinou o KME. |                   Reimporte o `ca.crt` correto. |
|            `401 Unauthorized` |              KME rejeitou o certificado do cliente (SAE). | Verifique se a CA assinou o certificado da SAE. |
|          `Connection refused` |                          Servidor Flask não está rodando. |        Verifique os logs em `/var/log/kme.log`. |
|        `bad command name qkd` |                       Versão do RouterOS inferior a 7.21. |                           Atualize para v7.22+. |

### 12.1 - Habilitar IPsec Debug e Logging   
```
/system logging add topics=ipsec,debug action=memory
/log print follow where topics~"ipsec"

```
### 12.2 - Inspecionar o Banco SQLite   
```
docker exec -it <kme> sqlite3 /opt/kme/kme.db \
  "SELECT key_id, master_sae_id, slave_sae_id, \
          delivered_master, delivered_slave FROM keys LIMIT 10;"

```
 --- 
## Parte 13: Reiniciando o Laboratório   
Lembre-se que as configurações de IP dentro do container Docker são perdidas ao reiniciar. Você deve reatribuir os IPs e reiniciar o script Python conforme descrito na **Parte 13** do documento original.   
 --- 
