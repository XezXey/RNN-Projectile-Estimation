from __future__ import print_function
import numpy as np
import torch
import matplotlib.pyplot as plt
import glob
import os
import argparse
from tqdm import tqdm
from rnn_model import RNN
from lstm_model import LSTM
from torch.utils.tensorboard import SummaryWriter
from torchvision.transforms import ToTensor
import PIL.Image
import io

def read_dataset(dataset_path, num_trajectory):
  # Dataset format
  # Trajectory path : (x0, y0) ... (xn, yn)
  # Initial conditon contain in : (x0, y0, angle, velocity, [time_limit] * n_samples, [g] * n_samples], n_samples)
  # List filename in a given path
  trajectory_filename = sorted(glob.glob(dataset_path + '/trajectory*.npy'))
  initial_condition_filename = sorted(glob.glob(dataset_path + '/initial_condition*.npy'))
  dataset_filename = list(zip(trajectory_filename, initial_condition_filename))
  all_initial_condition = []
  all_trajectory = []
  for i in tqdm(range(num_trajectory), desc='Loading Trajectory'):
    trajectory = (np.load(dataset_filename[i][0], allow_pickle=True))
    initial_condition = (np.load(dataset_filename[i][1], allow_pickle=True))

    # Cast to numpy array
    trajectory = np.array(trajectory)
    initial_condition = np.array(initial_condition)
    # Adding each timestep to the initial_condition and remove all columns except x0, y0
    initial_condition = np.array([np.column_stack((np.delete(np.tile(A=[initial_condition[i]], reps=(np.int(initial_condition[i][-1]), 1)), [0, 1, 2, 3, 4, 5, 6], axis=1), trajectory[i][:, :])) for i in range(initial_condition.shape[0])])
    # Clip the timestep out from trajectory
    trajectory = np.array([trajectory[i][1:, :-1] for i in range(initial_condition.shape[0])])
    # Clip the timestep out from initial_condition
    initial_condition = np.array([initial_condition[i][0:-1, :-1] for i in range(initial_condition.shape[0])])
    # Padding the trajectory and initial_condition to have the same length in its batch ===> For batch training
    initial_condition = ([torch.from_numpy(initial_condition[i]) for i in range(initial_condition.shape[0])])
    all_initial_condition.append(torch.nn.utils.rnn.pad_sequence(initial_condition, batch_first=True, padding_value=0).cuda())
    trajectory = ([torch.from_numpy(trajectory[i]) for i in range(trajectory.shape[0])])
    all_trajectory.append(torch.nn.utils.rnn.pad_sequence(trajectory, batch_first=True, padding_value=0).cuda())

  # longest_trajectory = len(max(trajectory, key=len))
  # for i in tqdm(range(initial_condition.shape[0]), desc='Padding'):
    # Padding the tail of the numpy array
    # trajectory_padding = np.tile(A=trajectory[i][-1], reps=np.int(longest_trajectory-initial_condition[i][-1]))
    # Append it back to the original array
    # trajectory[i] = np.append(trajectory[i], trajectory_padding.reshape(np.int(longest_trajectory-initial_condition[i][-1]), 2), axis=0)
  return all_trajectory, all_initial_condition

def visualize_trajectory(output, trajectory, writer, flag='predict'):
  # Reshape to (#N_Trajectory, Trajectory_length, (x, y))
  if flag=='train':
    output = output.reshape(trajectory.shape[0], trajectory.shape[1], 2)
  # print('Visualize')
  # print(output.shape)
  # print(trajectory.shape)
  vis_idx = np.random.randint(low=0, high=trajectory.shape[0], size=(5))
  traj_img_list = []
  for i in vis_idx:
    img_buffer = io.BytesIO()
    plt.title('Trajectory Estimation')
    plt.cla()
    # if flag != 'train':
      # print(output[i].shape)
      # print(trajectory[i].shape)
    plt.scatter(output[i][..., 0], output[i][..., 1], marker='^', label='Estimated')
    plt.scatter(trajectory[i][..., 0], trajectory[i][..., 1], marker='*', label='Ground Truth')
    plt.legend()
    plt.savefig(img_buffer, format='png')
    img_buffer.seek(0)
    traj_img = PIL.Image.open(img_buffer)
    traj_img = ToTensor()(traj_img)
    traj_img_list.append(np.array(traj_img[:3, ...]))   # Remove 4th channel which is transparency for PNG format
  return np.array(traj_img_list)

def unpadded_tensor(trajectory_padded, initial_condition_padded):
  trajectory_unpadded = []
  initial_condition_unpadded = []
  for i in tqdm(range(trajectory_padded.shape[0]), desc='Unpadding...'):
    trajectory_unpadded.append(np.delete(trajectory_padded[i], np.where(~trajectory_padded[i].any(axis=1))[0], axis=0))
    initial_condition_unpadded.append(np.vstack((np.zeros((1, 2)), np.delete(initial_condition_padded[i], np.where(~initial_condition_padded[i].any(axis=1))[0], axis=0))))
    #[************] Use this below line instead if the origin is not zeros [************]
    # initial_condition_unpadded.append(np.delete(initial_condition_padded[i], np.where(~initial_condition_padded[i].any(axis=1))[0], axis=0))

  return np.array(trajectory_unpadded), np.array(initial_condition_unpadded)

def train(trajectory_train, initial_condition_train, model, trajectory_val, initial_condition_val, hidden, cell_state, visualize_trajectory_flag=True, writer=None, min_val_loss=2e10, model_checkpoint_path='./model/'):
  # print('trajectory_train torch tensor : ', trajectory_train.size())
  # print('Initial condition torch tensor : ', initial_condition_train.size())
  # Training RNN/LSTM model 
  # Run over each example
  # trajectory_train = trajectory_train path with shape (n_trajectory_train, 2) ===> All 2 features are (x0, y0) ... (xn, yn) ;until yn == 0
  # initial_condition_train = Initial conditon with shape (n_trajectory_train, 6) ===> All 6 features are (x, y, angle, velocity, g, timestep)
  # Define models parameters
  learning_rate = 0.001
  n_epochs = 700
  optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
  loss_fn = torch.nn.MSELoss()
  # Initial hidden layer for the first RNN Cell
  batch_size = trajectory_train.size(0)
  # hidden = model.initHidden(batch_size=batch_size)
  # cell_state = model.initCellState(batch_size=batch_size)
  model.train()
  # Train a model
  for epoch in range(1, n_epochs+1):
    optimizer.zero_grad() # Clear existing gradients from previous epoch
    # Forward PASSING
    # Forward pass for training a model
    initial_condition_train = initial_condition_train.to(device)
    output_train, (hidden, cell_state) = model(initial_condition_train, hidden, cell_state)
    # Detach for use hidden as a weights in next batch
    cell_state.detach()
    cell_state = cell_state.detach()
    hidden.detach()
    hidden = hidden.detach()
    # Forward pass for validate a model
    initial_condition_val = initial_condition_val.to(device)
    output_val, (_, _) = model(initial_condition_val, hidden, cell_state)

    output_val.view((trajectory_val.size()[0], trajectory_val.size()[1], trajectory_val.size()[2]))
    val_loss = torch.sqrt(loss_fn(output_val, trajectory_val.view(-1, 2).float()))
    train_loss = torch.sqrt(loss_fn(output_train, trajectory_train.view(-1, 2).float()))
    train_loss.backward() # Perform a backpropagation and calculates gradients
    optimizer.step() # Updates the weights accordingly to the gradients

    if epoch%10 == 0:
      print('Epoch : {}/{}.........'.format(epoch, n_epochs), end='')
      print('Train Loss : {:.3f}'.format(train_loss.item()), end=', ')
      print('Val Loss : {:.3f}'.format(val_loss.item()))
      writer.add_scalar('Loss/train_loss', train_loss.item(), epoch)
      writer.add_scalar('Loss/val_loss', val_loss.item(), epoch)
      if visualize_trajectory_flag == True:
        traj_train_img = visualize_trajectory(output_train.cpu().detach().numpy(), trajectory_train.cpu().detach().numpy(), writer=writer, flag='train')
        writer.add_image('Training set : Trajectory Estimation', traj_train_img, epoch, dataformats='NCHW')
        traj_val_img = visualize_trajectory(output_val.cpu().detach().numpy(), trajectory_val.cpu().detach().numpy(), writer=writer, flag='train')
        writer.add_image('Validation set : Trajectory Estimation', traj_val_img, epoch, dataformats='NCHW')
      # Save model checkpoint
      if min_val_loss > val_loss:
        print('[#]Saving a model checkpoint')
        min_val_loss = val_loss
        torch.save(model.state_dict(), args.model_checkpoint_path)

  return min_val_loss, hidden, cell_state

def predict(trajectory_gt, initial_condition_gt, model, visualize_trajectory_flag=True, writer=None):
  trajectory_gt_unpadded, initial_condition_gt_unpadded = np.copy(unpadded_tensor(np.copy(trajectory_gt.cpu().detach().clone().numpy()), np.copy(initial_condition_gt.cpu().detach().clone().numpy())))
  # Trajectory size = (#n trajectory, #seq_length, #n_output_coordinates)
  output_pred = np.copy(initial_condition_gt_unpadded)
  # output_pred = np.insert(output_pred, output_pred.shape[1], values=[-10, -10], axis=1)
  # Initial condition size = (#n trajectory, #seq_length, #n_input_coordinates)delta in vector space
  initial_condition_pred = np.copy(initial_condition_gt_unpadded)
  # Loop over every trajectory
  loss_fn = torch.nn.MSELoss()
  n_prior_point = 20
  model.eval()
  with torch.no_grad():
    for i in tqdm(range(trajectory_gt_unpadded.shape[0]), desc='Prediction Trajectory'):
      # Loop over length of the trajectory
      batch_size=1
      hidden = model.initHidden(batch_size=batch_size)
      cell_state = model.initCellState(batch_size=batch_size)
      # print(trajectory_gt_unpadded[i].shape)
      # print(initial_condition_gt_unpadded[i].shape)
      # print(output_pred[i].shape)
      for j in range(n_prior_point, trajectory_gt_unpadded[i].shape[0]):
        # print('All points {} : From {} to {}'.format(trajectory_gt_unpadded[i].shape[0], j-n_prior_point, j))
        # Init the initial_condition_pred from initial_condition_gt for the beginning of the trajectory
        # Make a prediction

        # For LSTM
        # output, (hidden, cell_state) = model(torch.from_numpy(initial_condition_pred[i][j-n_prior_point:j].reshape(1, n_prior_point, 2)).cuda().float(), hidden, cell_state)
        # For RNN
        output, (hidden, cell_state) = model(torch.from_numpy(output_pred[i][j-n_prior_point:j].reshape(1, n_prior_point, 2)).cuda().float(), hidden, cell_state)
        try:
          output_pred[i][j] = np.copy(output[-1][:].cpu().detach().clone().numpy())
        except IndexError:
          output_pred[i] = np.vstack((output_pred[i], output[-1][:].cpu().detach().clone().numpy()))

        # print("LAST : ", model(initial_condition_pred[i][j-n_prior_point:j].reshape(1, n_prior_point, 2).float())[0][-1][:])
        # print("FIRST : ", model(initial_condition_pred[i][j-n_prior_point:j].reshape(1, n_prior_point, 2).float())[0][:][:])
        # Change the current point from predicted point not the ground truth point
        # initial_condition_pred[i][j][0] = np.copy(output_pred[i][j][0])
        # initial_condition_pred[i][j][1] = np.copy(output_pred[i][j][1])
        # print('=============={}=============='.format(j))
        # print('Input : ', output_pred[i][j-n_prior_point:j])
        # print('Prediction : ', output_pred[i][j])
        # print('Ground Truth : ', trajectory_gt[i][j-1])
      # print('Loss : ', loss_fn(torch.from_numpy(output_pred[i][:]).cuda().float(), torch.from_numpy(trajectory_gt_unpadded[i][:]).cuda().float()))
    if visualize_trajectory_flag == True:
      traj_pred_img = visualize_trajectory(output_pred, trajectory_gt_unpadded, writer=writer)
      writer.add_image('Testing set : Trajectory Estimation', traj_pred_img, dataformats='NCHW')


if __name__ == '__main__':
  print('[#]Trajectory Estimation')
  # Argumentparser for input
  parser = argparse.ArgumentParser(description='Predict the 2D projectile')
  parser.add_argument('--dataset_path', dest='dataset_path', type=str, help='Path to dataset', required=True)
  parser.add_argument('--num_trajectory', dest='num_trajectory', type=int, help='Number of trajectory to be use', default=3)
  parser.add_argument('--visualize_trajectory_flag', dest='visualize_trajectory_flag', type=bool, help='Visualize the trajectory', default=True)
  parser.add_argument('--model_checkpoint_path', dest='model_checkpoint_path', type=str, help='Path to save a model checkpoint', required=True)
  parser.add_argument('--model_path', dest='model_path', type=str, help='Path to load a trained model checkpoint', default=None)
  args = parser.parse_args()

  # Read dataset directory
  trajectory, initial_condition = read_dataset(args.dataset_path, args.num_trajectory)
  # Dataset format
  # Trajectory path : (x0, y0) ... (xn, yn)
  # Initial conditon contain in : (x0, y0, angle, velocity, [time_limit] * n_samples, [g] * n_samples], n_samples)
  print('[*]Dataset shape')
  print('===>Trajectory shape : ', [trajectory[i].size() for i in range(args.num_trajectory)])
  print('===>Initial condition shape : ', [initial_condition[i].size() for i in range(args.num_trajectory)])
  print('[*]Example trajectory (batch_size, n_trajectory, n_feature)')
  print('===>Shape of trajectory (x, y) : ', trajectory[0].size())
  print('===>Shape of initial_condition (x, y, angle, velocity, g, timestep) : ', initial_condition[0].size())
  print('===>Sample trajectory : ', trajectory[0][0][:])
  print('===>Sample initial_condition : ', initial_condition[0][0][:])

  # Model definition
  hidden_dim = 64
  n_output_coordinates = 2
  n_input_initial_condition = 2 # Contain following this trajectory parameters (x, y, angle, velocity, g, timestep)
  min_val_loss = 2e10
  # Torch GPU initialization
  if torch.cuda.is_available():
    device = torch.device('cuda')
    print('[%]GPU Enabled')
  else:
    device = torch.device('cpu')
    print('[%]GPU Disabled, CPU Enabled')

  print('[#]Model Architecture')
  if args.model_path is None:
    # Create a model
    print('===>No trained model')
    rnn_model = LSTM(input_size=n_input_initial_condition, output_size=n_output_coordinates, hidden_dim=hidden_dim, n_layers=8)
  else:
    print('===>Load trained model')
    rnn_model = LSTM(input_size=n_input_initial_condition, output_size=n_output_coordinates, hidden_dim=hidden_dim, n_layers=8)
    rnn_model.load_state_dict(torch.load(args.model_path))
  rnn_model = rnn_model.to(device)
  print(rnn_model)
  # Initial writer for tensorboard
  writer = SummaryWriter('trajectory_tensorboard/{}'.format(args.dataset_path))

  batch_size = trajectory[0].size()[0]
  hidden = rnn_model.initHidden(batch_size=batch_size)
  cell_state = rnn_model.initCellState(batch_size=batch_size)
  # Training a model
  # Convert from numpy to torch tensor
  test_fold = args.num_trajectory-1
  val_fold = args.num_trajectory-2
  for i in range(args.num_trajectory-2):
    # initial_condition_feed = initial_condition[i][np.newaxis, ...]
    # initial_condition_feed = torch.from_numpy(initial_condition_feed).cuda()
    initial_condition_feed = initial_condition[i].float()
    # trajectory_feed = trajectory[i]#[np.newaxis, ...]
    # trajectory_feed = torch.Tensor(trajectory_feed).cuda()
    trajectory_feed = trajectory[i].float()
    min_val_loss, hidden, cell_state = train(trajectory_train=trajectory_feed, initial_condition_train=initial_condition_feed, trajectory_val=trajectory[val_fold].float(), initial_condition_val=initial_condition[val_fold].float(), model=rnn_model, hidden=hidden, cell_state=cell_state, visualize_trajectory_flag=args.visualize_trajectory_flag, writer=writer, min_val_loss=min_val_loss, model_checkpoint_path=args.model_checkpoint_path)

  # Prediction on test set
  predict(trajectory_gt=trajectory[0][:], initial_condition_gt=initial_condition[0][:], model=rnn_model, visualize_trajectory_flag=args.visualize_trajectory_flag, writer=writer)

